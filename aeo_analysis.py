import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urljoin
import extruct
from w3lib.html import get_base_url
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ========== CONFIGURATION ==========
CONFIG = {
    "target_url": "https://www.example.com/",
    "max_pages": 10,
    "timeout": 10,
    "user_agent": "AEO-AuditBot/1.0 (+https://yourdomain.com)",
    "snippet_thresholds": {
        "avg_paragraph": 60,
        "max_paragraph": 120,
        "min_listed_pages_ratio": 0.5
    }
}

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ========== SESSION SETUP ==========
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.headers.update({'User-Agent': CONFIG['user_agent']})

# ========== GEMINI CONFIGURATION ==========
# Get API key from environment variable
api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
    raise ValueError("GEMINI_API_KEY environment variable not found. Please add it to your .env file.")

genai.configure(api_key=api_key)

# ========== MAIN FUNCTION ==========
def run_full_aeo_pipeline(target_url):
    base = target_url.strip().rstrip('/') + '/'
    results = {
        "structured_data": {"score": 0, "schema_types_found": {}, "pages_with_errors": [], "issues": []},
        "snippet_optimization": {"score": 0, "pages_evaluated": [], "overall_findings": {}, "issues": []},
        "crawlability": {"score": 0, "robots_txt": {}, "sitemap": {}, "issues": []},
        "aeo_score_raw": 0,
        "aeo_score_pct": 0
    }

    def is_blocked(bot, disallows):
        return '/' in disallows.get(bot, []) or '/' in disallows.get('*', [])

    # Crawlability check
    robots_url = urljoin(base, 'robots.txt')
    disallows = {}
    try:
        rob = session.get(robots_url, timeout=CONFIG['timeout'])
        accessible = (rob.status_code == 200)
        results['crawlability']['robots_txt']['accessible'] = accessible
        for line in rob.text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'): continue
            key, _, val = line.partition(':')
            key, val = key.strip().lower(), val.strip()
            if key == 'user-agent':
                current = val
                disallows.setdefault(current, [])
            elif key == 'disallow' and current:
                if val: disallows[current].append(val)
            elif key == 'sitemap':
                results['crawlability']['robots_txt'].setdefault('sitemap_urls', []).append(val)
        for bot in ['gptbot', 'googlebot']:
            results['crawlability']['robots_txt'][f'{bot}_blocked'] = is_blocked(bot, disallows)
        results['crawlability']['robots_txt']['chatbot_access'] = {}
        for name, ua in {'ChatGPT':'gptbot','Gemini':'gemini','Perplexity':'perplexity'}.items():
            combined = list(set(disallows.get(ua, []) + disallows.get('*', [])))
            results['crawlability']['robots_txt']['chatbot_access'][name] = {
                "allowed": not ('/' in combined),
                "disallowed_paths": combined
            }
    except Exception as e:
        logging.warning(f"robots.txt fetch failed: {e}")
        results['crawlability']['robots_txt']['accessible'] = False

    # Sitemap/page discovery
    pages_to_fetch = {base}
    sitemap_urls = results['crawlability']['robots_txt'].get('sitemap_urls') or [urljoin(base, 'sitemap.xml'), urljoin(base, 'sitemap_index.xml')]
    for s_url in sitemap_urls:
        try:
            r = session.get(s_url, timeout=CONFIG['timeout'])
            if r.status_code == 200 and r.text.strip().startswith('<?xml'):
                soup = BeautifulSoup(r.text, 'xml')
                for loc in soup.find_all('loc')[:CONFIG['max_pages']]:
                    pages_to_fetch.add(loc.text.strip())
                results['crawlability']['sitemap']['found'] = True
                break
        except Exception:
            continue
    if len(pages_to_fetch) == 1:
        try:
            home = session.get(base, timeout=CONFIG['timeout'])
            if home.status_code == 200:
                soup = BeautifulSoup(home.text, 'html.parser')
                for a in soup.select('a[href]'):
                    u = urljoin(base, a['href'])
                    if u.startswith(base) and not any(x in u for x in ['/privacy','/terms','/login','/signup']):
                        pages_to_fetch.add(u)
                        if len(pages_to_fetch) >= CONFIG['max_pages']: break
        except Exception:
            pass

    pages_list = list(pages_to_fetch)[:CONFIG['max_pages']]
    results['crawlability']['sitemap']['urls_analyzed'] = pages_list

    summary = {'total_paragraphs':0, 'total_length':0, 'max_length':0, 'pages_with_lists':0, 'pages_with_q':0}
    schema_counts = {}

    for url in pages_list:
        try:
            r = session.get(url, timeout=CONFIG['timeout'])
            if r.status_code != 200 or 'text/html' not in r.headers.get('Content-Type', ''): continue
            soup = BeautifulSoup(r.text, 'html.parser')
            paragraphs = soup.find_all('p')
            word_counts = [len(p.get_text(strip=True).split()) for p in paragraphs]
            avg_words = sum(word_counts)//len(word_counts) if word_counts else 0
            max_words = max(word_counts) if word_counts else 0
            list_items = sum(len(lst.find_all('li')) for lst in soup.find_all(['ul','ol']))
            table_count = len(soup.find_all('table'))
            q_count = sum(1 for h in soup.find_all(['h1','h2','h3','h4','h5','h6']) if '?' in h.get_text())
            summary['total_paragraphs'] += len(word_counts)
            summary['total_length'] += sum(word_counts)
            summary['max_length'] = max(summary['max_length'], max_words)
            summary['pages_with_lists'] += bool(list_items)
            summary['pages_with_q'] += bool(q_count)
            page_schemas = []
            base_url = get_base_url(r.text, r.url)
            data = extruct.extract(r.text, base_url=base_url, syntaxes=['json-ld'])
            def _rec(e):
                if isinstance(e, dict):
                    t = e.get('@type')
                    if t:
                        for typ in (t if isinstance(t, list) else [t]):
                            schema_counts[typ] = schema_counts.get(typ, 0) + 1
                            page_schemas.append(typ)
                    for v in e.values(): _rec(v)
                elif isinstance(e, list):
                    for i in e: _rec(i)
            for entry in data.get('json-ld', []):
                _rec(entry)
            results['snippet_optimization']['pages_evaluated'].append({
                'url': url,
                'avg_paragraph_words': avg_words,
                'max_paragraph_words': max_words,
                'paragraph_count': len(word_counts),
                'list_items': list_items,
                'table_count': table_count,
                'question_headings': q_count,
                'schema_types': list(set(page_schemas))
            })
        except Exception as e:
            results['structured_data']['pages_with_errors'].append({'url': url, 'error': str(e)})

    total_pages = len(results['snippet_optimization']['pages_evaluated'])
    if total_pages > 0:
        avg_len = summary['total_length'] // summary['total_paragraphs'] if summary['total_paragraphs'] else 0
        score = 10
        thr = CONFIG['snippet_thresholds']
        if avg_len > thr['avg_paragraph']: score -= 1; results['snippet_optimization']['issues'].append(f"High avg paragraph length: {avg_len}")
        if summary['max_length'] > thr['max_paragraph']: score -= 1; results['snippet_optimization']['issues'].append(f"Paragraph > {thr['max_paragraph']} words")
        if summary['pages_with_lists'] < total_pages * thr['min_listed_pages_ratio']: score -= 1; results['snippet_optimization']['issues'].append("Few pages have lists")
        if summary['pages_with_q'] == 0: score -= 1; results['snippet_optimization']['issues'].append("No question headings")
        results['snippet_optimization']['score'] = max(0, score)
        results['snippet_optimization']['overall_findings'] = {
            'avg_paragraph': avg_len,
            'max_paragraph': summary['max_length'],
            'pages_with_lists': summary['pages_with_lists'],
            'pages_with_questions': summary['pages_with_q'],
            'total_pages': total_pages,
            'evaluated_urls': [p['url'] for p in results['snippet_optimization']['pages_evaluated']],
            'pages_details': results['snippet_optimization']['pages_evaluated']
        }

    aeo_types = {"FAQPage","HowTo","QAPage","Recipe","HowToStep","NutritionInformation","BreadcrumbList","AggregateRating"}
    if any(t in aeo_types for t in schema_counts):
        results['structured_data']['score'] = 10
    elif schema_counts:
        results['structured_data']['score'] = 5; results['structured_data']['issues'].append("No AEO-specific schema types")
    else:
        results['structured_data']['score'] = 2; results['structured_data']['issues'].append("No JSON-LD found")
    results['structured_data']['schema_types_found'] = schema_counts

    crawl = 10
    rt = results['crawlability']['robots_txt']
    if not rt.get('accessible'): crawl -= 1; results['crawlability']['issues'].append("robots.txt inaccessible")
    if rt.get('googlebot_blocked'): crawl = 0; results['crawlability']['issues'].append("Googlebot blocked")
    if rt.get('gptbot_blocked'): crawl -= 2; results['crawlability']['issues'].append("GPTBot blocked")
    if not results['crawlability']['sitemap'].get('found'): crawl -= 1; results['crawlability']['issues'].append("No sitemap found")
    results['crawlability']['score'] = max(0, crawl)

    MAX_AEO_SCORE = 30
    raw_aeo_score = results['structured_data']['score'] + results['snippet_optimization']['score'] + results['crawlability']['score']
    aeo_percentage = round((raw_aeo_score / MAX_AEO_SCORE) * 100, 2)
    results['aeo_score_raw'] = raw_aeo_score
    results['aeo_score_pct'] = aeo_percentage

    # Get Gemini suggestions
    prompt = f"""
You are an Answer Engine Optimization (AEO) expert. Analyze the following AEO audit results JSON for a website and suggest the 10 most important changes to improve the site's AEO performance and rankings.
Each suggestion must be actionable, clear, specific, categorized, and prioritized.
Respond ONLY with:
{{"optimizations": [{{"description": "...", "impact_level": "High", "category": "..."}}, ...]}}

Here is the AEO audit output:
{json.dumps(results, indent=2)}
"""
    model = genai.GenerativeModel("gemini-2.5-flash-lite-preview-06-17")
    response = model.generate_content(prompt)
    raw = response.text.strip()
    try:
        start = raw.find('{')
        end = raw.rfind('}') + 1
        json_str = raw[start:end]
        optimizations = json.loads(json_str)
    except Exception as e:
        print("⚠️ Failed to parse Gemini's response as JSON.")
        print("Raw output:\n", raw)
        raise e

    # After chatbot_access is set up, add per-model scores
    model_scores = {}
    for model, info in results['crawlability']['robots_txt']['chatbot_access'].items():
        allowed = info['allowed']
        disallowed = info.get('disallowed_paths', [])
        if allowed and not disallowed:
            model_scores[model] = 100
        elif allowed and disallowed:
            model_scores[model] = 70
        else:
            model_scores[model] = 0
    results['model_scores'] = model_scores

    # Combine audit and optimizations
    return {
        "audit_report": results,
        "optimization_recommendations": optimizations
    }

# ========== RUNNING ENTRY POINT ==========
if __name__ == "__main__":
    url="https://healthline.com/"
    final_output = run_full_aeo_pipeline(url)
    print(json.dumps(final_output, indent=2))
