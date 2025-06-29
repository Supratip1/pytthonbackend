# Enhanced AEO Analysis Tool

A Python tool that analyzes websites for Answer Engine Optimization (AEO) and compares them with competitors.

## 🚀 What It Does

This tool analyzes a website and gives it an AEO score (0-100%) based on three main factors:
- **Structured Data** (0-10 points): JSON-LD markup, schema types
- **Snippet Optimization** (0-10 points): Content quality, readability, lists, questions
- **Crawlability** (0-10 points): robots.txt, sitemap, bot access

Then it finds competitors and ranks all sites together.

## 📁 Files

- `enhanced_aeo_analysis.py` - Main analysis tool
- `test_fixes.py` - Tests to verify everything works
- `README.md` - This file

## 🔧 How to Use

### Basic Setup
```bash
# Install dependencies
pip install requests beautifulsoup4 extruct google-generativeai python-dotenv

# Set your API key (optional - tool works without it)
echo "GEMINI_API_KEY=your_key_here" > .env
```

### Run Analysis
```python
from enhanced_aeo_analysis import run_with_competitors

# Full analysis with competitors
result = run_with_competitors("https://example.com")
print(result)

# Just audit without competitors
from enhanced_aeo_analysis import run_audit_only
audit = run_audit_only("https://example.com")
print(audit)
```

## 📊 Output Structure

```json
{
  "status": "success",
  "target_domain": "https://example.com",
  "audit_report": {
    "aeo_score": 73.33,           // Overall score (0-100%)
    "aeo_score_raw": 22,          // Raw points (0-30)
    "structured_data": { /* structured data analysis */ },
    "snippet_optimization": { /* content analysis */ },
    "crawlability": { /* technical analysis */ }
  },
  "optimization_recommendations": { /* AI suggestions */ },
  "competitor_analysis": {
    "your_ranking": 3,            // Your position (1 = best)
    "ranking": [                  // All sites ranked
      {
        "rank": 1,
        "domain": "competitor.com",
        "score": 85.0,
        "is_user_site": false,
        "key_advantages": ["Better structured data (8/10 vs 2/10)"],
        "key_disadvantages": []
      },
      {
        "rank": 3,
        "domain": "yoursite.com",
        "score": 73.33,
        "is_user_site": true,
        "key_advantages": [],
        "key_disadvantages": []
      }
    ]
  }
}
```

## 🧠 How It Works

### 1. Website Analysis
- Fetches robots.txt and sitemap
- Crawls pages (up to 10 by default)
- Extracts structured data (JSON-LD)
- Analyzes content (paragraphs, lists, questions)
- Calculates scores for each category

### 2. Competitor Discovery
- Uses Gemini AI to find 3-5 competitors
- Runs same analysis on each competitor
- Compares scores and generates insights

### 3. Ranking & Insights
- Ranks all sites by score (highest first)
- Identifies key advantages/disadvantages
- Provides actionable recommendations

## ⚙️ Configuration

```python
CONFIG = {
    "max_pages": 10,              # Max pages to analyze
    "timeout": 10,                # Request timeout
    "snippet_thresholds": {
        "avg_paragraph": 60,      # Max avg words per paragraph
        "max_paragraph": 120,     # Max words in any paragraph
        "min_listed_pages_ratio": 0.5  # Min % of pages with lists
    }
}
```

## 🔍 Key Functions

- `run_audit_only(url)` - Basic website analysis
- `run_full_aeo_pipeline(url)` - Analysis + AI recommendations
- `run_with_competitors(url)` - Full analysis with competitor comparison
- `validate_scores(results)` - Ensures scores are within bounds

## 🚨 Error Handling

- Works without API key (limited functionality)
- Graceful handling of network errors
- Continues analysis even if some pages fail
- Validates all scores to prevent impossible values

## 🧪 Testing

```bash
python test_fixes.py
```

Tests score validation and basic functionality.

## 💡 Tips for Developers

1. **API Key**: Set `GEMINI_API_KEY` in `.env` for full functionality
2. **Customization**: Modify `CONFIG` to adjust analysis parameters
3. **Integration**: Use `run_audit_only()` for basic analysis without AI
4. **Error Handling**: All functions return valid JSON even on errors
5. **Performance**: Analysis takes 30-60 seconds per site

## 🔧 Troubleshooting

- **No competitors found**: Check API key or network connection
- **Empty recommendations**: API key missing or invalid
- **Scores seem wrong**: Check `validate_scores()` function
- **Slow performance**: Reduce `max_pages` in config

That's it! The tool is designed to be simple to use and understand. 🎯 