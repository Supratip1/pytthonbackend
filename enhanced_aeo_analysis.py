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

# Handle import for both direct execution and module import
try:
    from app.config import settings
except ImportError:
    try:
        # Try to import from parent directory
        import sys
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from app.config import settings
    except ImportError:
        # Fallback for direct execution
        from dataclasses import dataclass
        
        @dataclass
        class Settings:
            GEMINI_API_KEY: str = os.getenv('GEMINI_API_KEY', '')
        
        settings = Settings()

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
# Disable logging to ensure clean JSON output
logging.getLogger().setLevel(logging.ERROR)

# ========== SESSION SETUP ==========
session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.headers.update({'User-Agent': CONFIG['user_agent']})

# ========== GEMINI CONFIGURATION ==========
api_key = settings.GEMINI_API_KEY
if api_key:
    genai.configure(api_key=api_key)
else:
    print("⚠️  GEMINI_API_KEY not found. Enhanced features will be limited.")
    print("💡 Set GEMINI_API_KEY environment variable for full functionality.")


# ========== VALIDATION FUNCTIONS ==========
def validate_scores(results):
    """Ensure all scores are within valid bounds"""
    # Validate structured data score
    results['structured_data']['score'] = min(10, max(0, results['structured_data']['score']))
    
    # Validate snippet optimization score
    results['snippet_optimization']['score'] = min(10, max(0, results['snippet_optimization']['score']))
    
    # Validate crawlability score
    results['crawlability']['score'] = min(10, max(0, results['crawlability']['score']))
    
    # Validate featured snippet readiness
    if 'featured_snippet_readiness' in results['snippet_optimization']:
        results['snippet_optimization']['featured_snippet_readiness'] = min(10, max(0, results['snippet_optimization']['featured_snippet_readiness']))
    
    # Recalculate final scores
    MAX_AEO_SCORE = 30
    raw_aeo_score = results['structured_data']['score'] + results['snippet_optimization']['score'] + results['crawlability']['score']
    raw_aeo_score = min(MAX_AEO_SCORE, raw_aeo_score)
    aeo_percentage = min(100.0, round((raw_aeo_score / MAX_AEO_SCORE) * 100, 2))
    
    results['aeo_score_raw'] = raw_aeo_score
    results['aeo_score_pct'] = aeo_percentage
    
    return results


# ========== MAIN FUNCTION ==========
def run_audit_only(target_url):
    """Run AEO audit without optimization recommendations"""
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
        # robots.txt fetch failed - continue silently
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
                    # Handle href attribute which can be string or list
                    href = a.get('href')
                    if isinstance(href, list):
                        href = href[0] if href else ''
                    if not href:
                        continue
                    u = urljoin(base, href)
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
            word_counts = [len(p.get_text(strip=True).split()) for p in paragraphs if p.get_text(strip=True)]
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
        # Prevent division by zero
        if summary['total_paragraphs'] > 0:
            avg_len = summary['total_length'] // summary['total_paragraphs']
        else:
            avg_len = 0
            
        score = 10
        thr = CONFIG['snippet_thresholds']
        
        # Enhanced issue reporting with impact levels and fixes
        if avg_len > thr['avg_paragraph']: 
            score -= 1; 
            results['snippet_optimization']['issues'].append({
                "issue": f"High avg paragraph length: {avg_len}",
                "impact": "Medium",
                "fix": "Break down paragraphs into shorter, scannable chunks under 60 words"
            })
        if summary['max_length'] > thr['max_paragraph']: 
            score -= 1; 
            results['snippet_optimization']['issues'].append({
                "issue": f"Paragraph > {thr['max_paragraph']} words",
                "impact": "High",
                "fix": "Split long paragraphs into multiple shorter ones"
            })
        if summary['pages_with_lists'] < total_pages * thr['min_listed_pages_ratio']: 
            score -= 1; 
            results['snippet_optimization']['issues'].append({
                "issue": "Few pages have lists",
                "impact": "Medium",
                "fix": "Add bullet points and numbered lists to improve readability"
            })
        if summary['pages_with_q'] == 0: 
            score -= 1; 
            results['snippet_optimization']['issues'].append({
                "issue": "No question headings",
                "impact": "High",
                "fix": "Add question-based headings (H1-H6) to target featured snippets"
            })
        
        # Enhanced content quality scoring (bonus points, but capped)
        readability_bonus = 0
        if avg_len < 50: readability_bonus += 1
        if avg_len < 30: readability_bonus += 1
        if summary['pages_with_lists'] > total_pages * 0.7: readability_bonus += 1
        if summary['pages_with_q'] > 0: readability_bonus += 1
        
        # Ensure snippet optimization score doesn't exceed 10
        results['snippet_optimization']['score'] = min(10, max(0, score + readability_bonus))
        results['snippet_optimization']['overall_findings'] = {
            'avg_paragraph': avg_len,
            'max_paragraph': summary['max_length'],
            'pages_with_lists': summary['pages_with_lists'],
            'pages_with_questions': summary['pages_with_q'],
            'total_pages': total_pages,
            'evaluated_urls': [p['url'] for p in results['snippet_optimization']['pages_evaluated']],
            'pages_details': results['snippet_optimization']['pages_evaluated'],
            'readability_score': readability_bonus
        }

    # Enhanced structured data scoring (capped at 10)
    aeo_types = {"FAQPage","HowTo","QAPage","Recipe","HowToStep","NutritionInformation","BreadcrumbList","AggregateRating"}
    aeo_schemas = [t for t in schema_counts if t in aeo_types]
    
    if aeo_schemas:
        # Weighted scoring based on number and types of AEO schemas, but capped at 10
        structured_score = min(10, len(aeo_schemas) * 2 + len(schema_counts) * 0.5)
        results['structured_data']['score'] = int(structured_score)
    elif schema_counts:
        results['structured_data']['score'] = 5; 
        results['structured_data']['issues'].append({
            "issue": "No AEO-specific schema types",
            "impact": "High",
            "fix": "Implement FAQ, HowTo, Recipe, or QAPage schema markup"
        })
    else:
        results['structured_data']['score'] = 2; 
        results['structured_data']['issues'].append({
            "issue": "No JSON-LD found",
            "impact": "Critical",
            "fix": "Add structured data markup to help search engines understand your content"
        })
    
    results['structured_data']['schema_types_found'] = schema_counts
    results['structured_data']['aeo_schemas_found'] = aeo_schemas

    # Featured snippet readiness calculation (separate metric, not part of main score)
    featured_snippet_score = 0
    if aeo_schemas: featured_snippet_score += 3
    if summary['pages_with_q'] > 0: featured_snippet_score += 2
    if avg_len < 50: featured_snippet_score += 2
    if list_items > 0: featured_snippet_score += 1
    if table_count > 0: featured_snippet_score += 1
    
    results['snippet_optimization']['featured_snippet_readiness'] = min(10, featured_snippet_score)

    # Crawlability scoring (capped at 10)
    crawl = 10
    rt = results['crawlability']['robots_txt']
    if not rt.get('accessible'): 
        crawl -= 1; 
        results['crawlability']['issues'].append({
            "issue": "robots.txt inaccessible",
            "impact": "Medium",
            "fix": "Ensure robots.txt is accessible at yourdomain.com/robots.txt"
        })
    if rt.get('googlebot_blocked'): 
        crawl = 0; 
        results['crawlability']['issues'].append({
            "issue": "Googlebot blocked",
            "impact": "Critical",
            "fix": "Remove Googlebot blocking from robots.txt immediately"
        })
    else:
        if rt.get('gptbot_blocked'): 
            crawl -= 2; 
            results['crawlability']['issues'].append({
                "issue": "GPTBot blocked",
                "impact": "Medium",
                "fix": "Consider allowing AI crawlers for better visibility in AI search results"
            })
        if not results['crawlability']['sitemap'].get('found'): 
            crawl -= 1; 
            results['crawlability']['issues'].append({
                "issue": "No sitemap found",
                "impact": "Medium",
                "fix": "Create and submit XML sitemap to search engines"
            })
    results['crawlability']['score'] = max(0, min(10, crawl))

    # Calculate final AEO score (ensuring it never exceeds 30)
    MAX_AEO_SCORE = 30
    raw_aeo_score = results['structured_data']['score'] + results['snippet_optimization']['score'] + results['crawlability']['score']
    
    # Ensure raw score doesn't exceed maximum
    raw_aeo_score = min(MAX_AEO_SCORE, raw_aeo_score)
    
    # Calculate percentage (ensuring it never exceeds 100%)
    aeo_percentage = min(100.0, round((raw_aeo_score / MAX_AEO_SCORE) * 100, 2))
    
    results['aeo_score_raw'] = raw_aeo_score
    results['aeo_score_pct'] = aeo_percentage

    # Final validation to ensure all scores are within bounds
    results = validate_scores(results)

    # Add model_scores based on chatbot_access
    model_scores = {}
    chatbot_access = results['crawlability']['robots_txt'].get('chatbot_access', {})
    for model, info in chatbot_access.items():
        allowed = info.get('allowed', False)
        disallowed = info.get('disallowed_paths', [])
        if allowed and not disallowed:
            model_scores[model] = 100
        elif allowed and disallowed:
            model_scores[model] = 70
        else:
            model_scores[model] = 0
    results['model_scores'] = model_scores

    return results


def run_full_aeo_pipeline(target_url):
    """Run complete AEO analysis including optimization recommendations"""
    # Get audit results
    audit_results = run_audit_only(target_url)
    
    # Get Gemini suggestions only if API key is available
    if not api_key:
        optimizations = {"optimizations": []}
    else:
        prompt = f"""
You are an Answer Engine Optimization (AEO) expert. Analyze the following AEO audit results JSON for a website and suggest the 10 most important changes to improve the site's AEO performance and rankings.
Each suggestion must be actionable, clear, specific, categorized, and prioritized.
Respond ONLY with valid JSON in this exact format:
{{"optimizations": [{{"description": "detailed description here", "impact_level": "High/Medium/Low", "category": "Structured Data/Snippet Optimization/Crawlability"}}, ...]}}

Here is the AEO audit output:
{json.dumps(audit_results, indent=2)}
"""
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(prompt)
            raw = response.text.strip()
            
            # Try to extract JSON from response
            start = raw.find('{')
            end = raw.rfind('}') + 1
            
            if start == -1 or end == 0:
                optimizations = {"optimizations": []}
            else:
                json_str = raw[start:end]
                # Validate JSON structure
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and 'optimizations' in parsed:
                    optimizations = parsed
                else:
                    optimizations = {"optimizations": []}
                    
        except Exception as e:
            # Fallback to empty optimizations on any error
            optimizations = {"optimizations": []}

    # Combine audit and optimizations
    return {
        "audit_report": audit_results,
        "optimization_recommendations": optimizations,
        "model_scores": audit_results.get("model_scores", {})
    }

    
def fetch_site_description(url):
    """Fetch site title and description using competitor.py approach"""
    try:
        resp = session.get(url, timeout=CONFIG['timeout'])
        if resp.status_code != 200:
            return {"title": "", "description": ""}
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        
        # Try multiple meta description sources
        desc = ""
        meta_desc = soup.find("meta", attrs={"name":"description"}) or soup.find("meta", attrs={"property":"og:description"})
        if meta_desc:
            try:
                content = meta_desc.get("content")
                if content:
                    desc = content.strip()
            except (KeyError, TypeError):
                desc = ""
        
        return {"title": title, "description": desc}
    except Exception as e:
        return {"title": "", "description": ""}

def get_competitor_links(domain):
    """Get competitor links using Gemini with enhanced site information"""
    if not api_key:
        return []
    
    # Fetch site description to provide context to Gemini
    site_info = fetch_site_description(domain)
    title = site_info.get("title", "")
    description = site_info.get("description", "")
    
    prompt = f"""
You are an expert in Answer Engine Optimization (AEO) competitor analysis. 

Given this website:
- Domain: {domain}
- Title: {title}
- Description: {description}

Identify exactly 5 direct competitors that:
1. Offer highly similar products or services
2. Operate in the same industry or niche
3. Target the same audience
4. Have strong AEO practices (rich structured data, optimized snippets, clear content hierarchy)

Provide only a JSON array of exactly 5 root URLs, no additional text.
Example: ["https://competitor1.com", "https://competitor2.com", "https://competitor3.com", "https://competitor4.com", "https://competitor5.com"]
"""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content(prompt)
        raw = response.text.strip()
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start == -1 or end == 0:
            return []
        json_str = raw[start:end]
        candidates = json.loads(json_str)
        # Return exactly 5 valid URLs without filtering
        valid_candidates = [url for url in candidates if url.startswith("http") or url.startswith("https")]
        return valid_candidates[:5]  # Ensure exactly 5 competitors
    except Exception as e:
        return []


def run_with_competitors(main_url):
    """Run AEO analysis with competitor comparison"""
    # Fetch site description for context
    site_info = fetch_site_description(main_url)
    
    # Get main site full analysis
    main_result = run_full_aeo_pipeline(main_url)
    
    # Get competitor links from Gemini
    competitor_links = get_competitor_links(main_url)
    
    # Run audit_only on each competitor
    competitor_results = []
    for comp_url in competitor_links:
        try:
            comp_audit = run_audit_only(comp_url)
            
            # Safely extract data with fallbacks
            schema_types = list(comp_audit['structured_data']['schema_types_found'].keys())
            structured_issues = comp_audit['structured_data']['issues'][:2] if comp_audit['structured_data']['issues'] else []
            snippet_issues = comp_audit['snippet_optimization']['issues'][:2] if comp_audit['snippet_optimization']['issues'] else []
            crawl_issues = comp_audit['crawlability']['issues'][:2] if comp_audit['crawlability']['issues'] else []
            
            overall_findings = comp_audit['snippet_optimization'].get('overall_findings', {})
            
            competitor_results.append({
                "domain": comp_url,
                "aeo_score": comp_audit['aeo_score_pct'],
                "structured_data_score": comp_audit['structured_data']['score'],
                "snippet_optimization_score": comp_audit['snippet_optimization']['score'],
                "crawlability_score": comp_audit['crawlability']['score'],
                "total_pages_analyzed": len(comp_audit['snippet_optimization']['pages_evaluated']),
                "schema_types_found": len(comp_audit['structured_data']['schema_types_found']),
                "issues_count": len(comp_audit['structured_data']['issues']) + len(comp_audit['snippet_optimization']['issues']) + len(comp_audit['crawlability']['issues']),
                "key_schema_types": schema_types[:5],
                "main_issues": structured_issues + snippet_issues + crawl_issues,
                "content_quality": {
                    "avg_paragraph_length": overall_findings.get('avg_paragraph', 0),
                    "pages_with_lists": overall_findings.get('pages_with_lists', 0),
                    "pages_with_questions": overall_findings.get('pages_with_questions', 0)
                },
                "technical_status": {
                    "robots_txt_accessible": comp_audit['crawlability']['robots_txt'].get('accessible', False),
                    "sitemap_found": comp_audit['crawlability']['sitemap'].get('found', False),
                    "googlebot_blocked": comp_audit['crawlability']['robots_txt'].get('googlebot_blocked', False)
                }
            })
        except Exception as e:
            # Skip failed competitors
            continue

    # Sort competitors by AEO score
    competitor_results.sort(key=lambda x: x['aeo_score'], reverse=True)
    
    # Create ranking analysis
    main_score = main_result['audit_report']['aeo_score_pct']
    
    if not competitor_results:
        # No competitors found
        ranking = [
            {
                "rank": 1,
                "domain": main_url,
                "score": main_score,
                "is_user_site": True
            }
        ]
        user_ranking = 1
        average_competitor_score = 0
    else:
        # Ensure all scores are within bounds (0-100)
        for comp in competitor_results:
            comp['aeo_score'] = min(100.0, max(0.0, comp['aeo_score']))
        
        # Create ranking list with all sites
        all_sites = [(main_score, main_result['audit_report']['structured_data']['score'], main_url, True)] + [(comp['aeo_score'], comp['structured_data_score'], comp['domain'], False) for comp in competitor_results]
        all_sites.sort(key=lambda x: (x[0], x[1]), reverse=True)  # Sort by AEO score first, then structured data score as tiebreaker
        
        # Generate ranking list
        ranking = []
        
        for i, (score, structured_score, domain, is_user) in enumerate(all_sites):
            # Calculate rank based on position (1-based indexing)
            current_rank = i + 1
            
            # Generate insights for each site
            key_advantages = []
            key_disadvantages = []
            
            if not is_user:
                # Compare competitor with user site
                score_diff_threshold = 1  # Reduced from 2 to 1 for more sensitive comparison
                
                # Find competitor data
                comp_data = next((comp for comp in competitor_results if comp['domain'] == domain), None)
                if comp_data:
                    if comp_data['structured_data_score'] > main_result['audit_report']['structured_data']['score'] + score_diff_threshold:
                        key_advantages.append(f"Better structured data ({comp_data['structured_data_score']}/10 vs {main_result['audit_report']['structured_data']['score']}/10)")
                    elif main_result['audit_report']['structured_data']['score'] > comp_data['structured_data_score'] + score_diff_threshold:
                        key_disadvantages.append(f"Worse structured data ({comp_data['structured_data_score']}/10 vs {main_result['audit_report']['structured_data']['score']}/10)")
                        
                    if comp_data['snippet_optimization_score'] > main_result['audit_report']['snippet_optimization']['score'] + score_diff_threshold:
                        key_advantages.append(f"Better content optimization ({comp_data['snippet_optimization_score']}/10 vs {main_result['audit_report']['snippet_optimization']['score']}/10)")
                    elif main_result['audit_report']['snippet_optimization']['score'] > comp_data['snippet_optimization_score'] + score_diff_threshold:
                        key_disadvantages.append(f"Worse content optimization ({comp_data['snippet_optimization_score']}/10 vs {main_result['audit_report']['snippet_optimization']['score']}/10)")
                        
                    if comp_data['crawlability_score'] > main_result['audit_report']['crawlability']['score'] + score_diff_threshold:
                        key_advantages.append(f"Better technical SEO ({comp_data['crawlability_score']}/10 vs {main_result['audit_report']['crawlability']['score']}/10)")
                    elif main_result['audit_report']['crawlability']['score'] > comp_data['crawlability_score'] + score_diff_threshold:
                        key_disadvantages.append(f"Worse technical SEO ({comp_data['crawlability_score']}/10 vs {main_result['audit_report']['crawlability']['score']}/10)")
            
            ranking.append({
                "rank": current_rank,
                "domain": domain,
                "score": score,
                "is_user_site": is_user,
                "key_advantages": key_advantages,
                "key_disadvantages": key_disadvantages
            })
        
        # Find user's ranking
        user_ranking = next(item['rank'] for item in ranking if item['is_user_site'])
        
        # Calculate average competitor score
        average_competitor_score = sum(comp['aeo_score'] for comp in competitor_results) / len(competitor_results)

    # Enhanced JSON structure with new metrics
    return {
        "status": "success",
        "target_domain": main_url,
        "link_details": {
            "title": site_info.get("title", ""),
            "description": site_info.get("description", "")
        },
        "audit_report": {
            "aeo_score": main_result['audit_report']['aeo_score_pct'],
            "aeo_score_raw": main_result['audit_report']['aeo_score_raw'],
            "featured_snippet_potential": main_result['audit_report']['snippet_optimization'].get('featured_snippet_readiness', 0),
            "content_quality_score": main_result['audit_report']['snippet_optimization'].get('overall_findings', {}).get('readability_score', 0),
            "technical_seo_score": main_result['audit_report']['crawlability']['score'],
            "structured_data_score": main_result['audit_report']['structured_data']['score'],
            "structured_data": {
                "score": main_result['audit_report']['structured_data']['score'],
                "schema_types_found": main_result['audit_report']['structured_data']['schema_types_found'],
                "aeo_schemas_found": main_result['audit_report']['structured_data'].get('aeo_schemas_found', []),
                "issues": main_result['audit_report']['structured_data']['issues']
            },
            "snippet_optimization": {
                "score": main_result['audit_report']['snippet_optimization']['score'],
                "overall_findings": main_result['audit_report']['snippet_optimization']['overall_findings'],
                "featured_snippet_readiness": main_result['audit_report']['snippet_optimization'].get('featured_snippet_readiness', 0),
                "issues": main_result['audit_report']['snippet_optimization']['issues']
            },
            "crawlability": {
                "score": main_result['audit_report']['crawlability']['score'],
                "robots_txt": main_result['audit_report']['crawlability']['robots_txt'],
                "sitemap": main_result['audit_report']['crawlability']['sitemap'],
                "issues": main_result['audit_report']['crawlability']['issues']
            }
        },
        "optimization_recommendations": main_result['optimization_recommendations'],
        "competitor_analysis": {
            "your_ranking": user_ranking,
            "total_competitors": len(competitor_results),
            "average_competitor_score": average_competitor_score,
            "score_difference": main_score - average_competitor_score,
            "ranking": ranking,
            "competitors": competitor_results
        }
    }

# ========== RUNNING ENTRY POINT ==========
if __name__ == "__main__":
    url = "https://www.superimpress.com/"
    final_output = run_with_competitors(url)
    print(json.dumps(final_output, indent=2)) 