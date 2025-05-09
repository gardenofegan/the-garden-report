#!/usr/bin/env python3

"""
The Garden Report: Gather top news (Hacker News), 
fetch weather for Lawrenceburg, optionally summarize with OpenAI, 
then output an old-school multi-column PDF and print it.
"""

import os
import sys
import subprocess
import datetime
import random
import json
import pickle
from pathlib import Path

import feedparser
import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    BaseDocTemplate,
    PageTemplate,
    Frame,
    Paragraph,
    Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import html2text
from babel.dates import format_date
import locale
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

load_dotenv()  # Load environment variables from .env file

# ------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------

# Hacker News
HN_TOP_STORIES_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"

# RSS Feeds and News Sites
EAGLE_COUNTRY_URL = "https://www.eaglecountryonline.com/news/local-news/feed.xml"
RTS_URL = "https://www.rts.ch/"
LE_TEMPS_RSS = "https://www.letemps.ch/articles.rss"

# Weather: Open-Meteo API
CITY_NAME = "Lawrenceburg"  # City name for display purposes
CITY_LAT = 39.0909  # city latitude
CITY_LON = -84.85  # city longitude
WEATHER_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={CITY_LAT}&longitude={CITY_LON}"
    f"&current=temperature_2m,weather_code&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
)

# (Optional) OpenAI Summarization
USE_OPENAI_SUMMARY = False
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Get from environment variable

# Printer Name (for 'lpr')
PRINTER_NAME = ""  # e.g., "EPSON_XXXX" or leave blank for default

# PDF output filename prefix
PDF_PREFIX = "morning_press"

# Max number of items to fetch per source
MAX_ITEMS = 5

# Default language for summaries
DEFAULT_LANGUAGE = "english"

# Summary configuration
SUMMARY_MAX_TOKENS = 300  # Increased from 150
SUMMARY_TEMPERATURE = 0.5  # Reduced for more focused summaries

# Set locale for date formatting
try:
    locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')
except:
    try:
        locale.setlocale(locale.LC_TIME, 'en_US')
    except:
        print("[WARN] Could not set US locale, falling back to default")

# Fallback quotes in French
FALLBACK_QUOTES = [
    {
        "quote": "La vie est courte, l'art est long.",
        "author": "Hippocrate"
    },
    {
        "quote": "Je pense, donc je suis.",
        "author": "René Descartes"
    },
    {
        "quote": "Un petit pas pour l'homme, un grand pas pour l'humanité.",
        "author": "Neil Armstrong"
    },
    {
        "quote": "La beauté est dans les yeux de celui qui regarde.",
        "author": "Oscar Wilde"
    },
    {
        "quote": "L'imagination est plus importante que le savoir.",
        "author": "Albert Einstein"
    },
    {
        "quote": "Le doute est le commencement de la sagesse.",
        "author": "Aristote"
    },
    {
        "quote": "La liberté des uns s'arrête là où commence celle des autres.",
        "author": "Jean-Paul Sartre"
    },
    {
        "quote": "Le hasard ne favorise que les esprits préparés.",
        "author": "Louis Pasteur"
    }
]

# ZenQuotes API
ZENQUOTES_API_URL = "https://zenquotes.io/api/random"

# Add to the configuration section
AFFIRMATIONS_CATEGORIES = [
    "confidence",
    "success",
    "motivation",
    "growth",
    "happiness",
    "health"
]

FALLBACK_AFFIRMATIONS = [
    "Je suis capable de réaliser de grandes choses aujourd'hui.",
    "Chaque jour, je deviens une meilleure version de moi-même.",
    "Je choisis d'être confiant(e) et positif(ve).",
    "Mes possibilités sont infinies.",
    "Je mérite le succès et le bonheur.",
    "Je transforme les défis en opportunités.",
    "Ma détermination est plus forte que mes peurs.",
    "Je suis reconnaissant(e) pour tout ce que j'ai.",
    "Mon potentiel est illimité.",
    "Je crée ma propre réalité positive."
]

# Register emoji font if available
try:
    # Try different possible paths for the Noto Color Emoji font
    emoji_font_paths = [
        "/System/Library/Fonts/Apple Color Emoji.ttc",  # macOS
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",  # Linux
        "C:/Windows/Fonts/seguiemj.ttf",  # Windows
    ]
    
    for font_path in emoji_font_paths:
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('EmojiFont', font_path))
            break
except Exception as e:
    print(f"[WARN] Could not register emoji font: {e}")

# Add to configuration section
SECTION_SEPARATOR = "*" * 20

# Add to configuration section
CACHE_DIR = "cache"
CACHE_FILE = "news_cache.pkl"

def save_to_cache(content):
    """Save content to cache file."""
    cache_path = Path(CACHE_DIR)
    cache_path.mkdir(exist_ok=True)
    
    cache_file = cache_path / CACHE_FILE
    with open(cache_file, 'wb') as f:
        pickle.dump({
            'timestamp': datetime.datetime.now(),
            'content': content
        }, f)

def load_from_cache():
    """Load content from cache file if it exists and is from today."""
    cache_path = Path(CACHE_DIR) / CACHE_FILE
    if not cache_path.exists():
        return None
        
    try:
        with open(cache_path, 'rb') as f:
            cache_data = pickle.load(f)
            
        # Check if cache is from today
        cache_date = cache_data['timestamp'].date()
        today = datetime.datetime.now().date()
        
        if cache_date == today:
            return cache_data['content']
    except Exception as e:
        print(f"[WARN] Could not load cache: {e}")
    
    return None

# ------------------------------------------------------
# OPTIONAL: OPENAI SUMMARIZATION
# ------------------------------------------------------
def summarize_text_with_openai(text, max_tokens=SUMMARY_MAX_TOKENS, temperature=SUMMARY_TEMPERATURE, language=DEFAULT_LANGUAGE):
    """
    Summarize a given text using OpenAI GPT-4 API.
    Returns an engaging newspaper-style summary in the specified language.
    """
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    if not OPENAI_API_KEY or not text.strip():
        return text

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": f"""You are an experienced newspaper editor who writes concise, impactful summaries.
                Write in {language}.
                Focus on the key points and maintain journalistic style.
                Be concise but ensure all important information is included.
                Aim for 2-3 short paragraphs maximum."""
            },
            {
                "role": "user",
                "content": f"Write a concise newspaper summary of this article. Focus on the most newsworthy elements:\n\n{text}"
            }],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        summary = response.choices[0].message.content.strip()
        return summary
    except Exception as e:
        print(f"[WARN] Could not summarize with OpenAI: {e}")
        return text

# ------------------------------------------------------
# DATA FETCHING FUNCTIONS
# ------------------------------------------------------
def fetch_hackernews_top_stories(limit=5, language=DEFAULT_LANGUAGE):
    """
    Fetch top stories from Hacker News and summarize their content.
    Returns a list of dictionaries with story details.
    Only includes articles that were successfully fetched and summarized.
    """
    result = []
    try:
        r = requests.get(HN_TOP_STORIES_URL, timeout=10)
        r.raise_for_status()
        top_ids = r.json()
        
        for story_id in top_ids:  # Remove limit here to process more if some fail
            if len(result) >= limit:  # Check if we have enough successful articles
                break
                
            # Fetch story details
            story_url = f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            s = requests.get(story_url, timeout=10)
            s.raise_for_status()
            story_data = s.json()
            
            title = story_data.get("title", "").strip()
            url = story_data.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
            
            # Skip if no title
            if not title:
                continue
            
            # Fetch and analyze content if there's a URL
            content_summary = ""
            if url and not url.startswith("https://news.ycombinator.com"):
                try:
                    # Use a browser-like User-Agent
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                    article_response = requests.get(url, timeout=10, headers=headers)
                    article_response.raise_for_status()
                    
                    # Use BeautifulSoup to extract article content
                    soup = BeautifulSoup(article_response.text, 'html.parser')
                    
                    # Remove script and style elements
                    for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                        script.decompose()
                    
                    # Get text content
                    text = soup.get_text()
                    
                    # Break into lines and remove leading/trailing space
                    lines = (line.strip() for line in text.splitlines())
                    # Break multi-headlines into a line each
                    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                    # Drop blank lines
                    text = ' '.join(chunk for chunk in chunks if chunk)
                    
                    # Verify we have meaningful content
                    # TODO: Add back in once I decide on AI summary use case
                    if len(text) > 200:  # Minimum content length threshold
                        content_summary = summarize_text_with_openai(
                            text[:8000],
                            language=language
                        )
                        # Only add to results if we got a summary
                        if content_summary.strip():
                            result.append({
                                "title": title,
                                "url": url,
                                "content_summary": content_summary
                            })
                    else:
                        print(f"[WARN] Article content too short or invalid for: {url}")
                        
                except Exception as e:
                    print(f"[WARN] Could not fetch/process article content: {e}")
            
    except Exception as e:
        print(f"[ERROR] Hacker News fetch error: {e}")
    return result

def fetch_rss_headlines(feed_url, limit=5, language=DEFAULT_LANGUAGE):
    """
    Fetch headlines and content from an RSS feed, returning a list of dicts with 'title', 'description'.
    """
    items = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:limit]:
            title = entry.title
            # Get the full description/content
            content = entry.description if hasattr(entry, 'description') else ''
            
            # If we have OpenAI enabled, summarize the content
            if USE_OPENAI_SUMMARY and content:
                content = summarize_text_with_openai(content, language=language)
            
            items.append({
                "title": title,
                "content": content
            })
    except Exception as e:
        print(f"[ERROR] RSS fetch error for {feed_url}: {e}")
    return items

def fetch_rss_headlines_with_details(feed_url, limit=5, language=DEFAULT_LANGUAGE):
    """
    Fetch headlines and content from an RSS feed, scrape the article link for each, and summarize with AI if content is long.
    Returns a list of dicts with 'title' and 'content'.
    """
    items = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:limit]:
            title = entry.title
            # Get the full description/content
            content = entry.description if hasattr(entry, 'description') else ''

            # Try to get the article link
            url = entry.link if hasattr(entry, 'link') else None
            article_text = ''
            if url:
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                    }
                    resp = requests.get(url, timeout=10, headers=headers)
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                        script.decompose()
                    text = soup.get_text()
                    lines = (line.strip() for line in text.splitlines())
                    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                    article_text = ' '.join(chunk for chunk in chunks if chunk)
                except Exception as e:
                    print(f"[WARN] Could not fetch/process article content: {e}")

            # If the article text is long, summarize it
            if article_text and len(article_text) > 1000:
                summary = summarize_text_with_openai(article_text[:8000], language=language)
                content = summary
            elif article_text:
                content = article_text
            else:
                # If no article text, keep the original description
                pass

            items.append({
                "title": title,
                "content": content
            })
    except Exception as e:
        print(f"[ERROR] RSS fetch error for {feed_url}: {e}")
    return items

def fetch_weather(city_url):
    """
    Fetch weather data from Open-Meteo API, returning a string description.
    """
    try:
        resp = requests.get(city_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if "current" in data:
            temp = data["current"]["temperature_2m"]
            weather_code = data["current"]["weather_code"]
            
            # WMO Weather interpretation codes (https://open-meteo.com/en/docs)
            weather_descriptions = {
                0: "Clear sky",
                1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
                45: "Foggy", 48: "Depositing rime fog",
                51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
                61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
                71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
                77: "Snow grains",
                80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
                85: "Slight snow showers", 86: "Heavy snow showers",
                95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail"
            }
            
            desc = weather_descriptions.get(weather_code, "Unknown conditions")
            return f"Weather in {CITY_NAME}: {temp}°C, {desc}"
        else:
            return "Weather data not found."
    except Exception as e:
        return f"[ERROR] Weather fetch: {e}"

def fetch_rts_news(limit=5, language=DEFAULT_LANGUAGE):
    """
    Scrape news from RTS website and use AI to select and summarize top stories.
    """
    items = []
    try:
        # Fetch the main page
        response = requests.get(RTS_URL, timeout=10)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Convert HTML to plain text for better processing
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        page_text = h.handle(str(soup))
        
        # Use AI to identify and extract top stories
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # First, let AI identify the most important stories
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": f"You are a news editor for RTS. Analyze the webpage content and identify the {limit} most important news stories. Focus on actual news articles, not TV shows or programs. Return the results in a structured format with title and content clearly separated."
            },
            {
                "role": "user",
                "content": f"Here's the RTS webpage content. Identify the {limit} most important news stories, extracting their titles and content. Format your response as 'TITLE: xxx\nCONTENT: yyy' for each story:\n\n{page_text}"
            }],
            max_tokens=1000,
            temperature=0.3
        )
        
        # Parse AI response and extract stories
        stories_text = response.choices[0].message.content.strip()
        story_blocks = stories_text.split('\n\n')
        
        for block in story_blocks:
            if not block.strip():
                continue
                
            lines = block.split('\n')
            title = ""
            content = ""
            
            for line in lines:
                if line.startswith("TITLE:"):
                    title = line.replace("TITLE:", "").strip()
                elif line.startswith("CONTENT:"):
                    content = line.replace("CONTENT:", "").strip()
            
            if title and content:
                # Summarize the content in the target language
                summary = summarize_text_with_openai(content, language=language)
                items.append({
                    "title": title,
                    "content": summary
                })
                
            if len(items) >= limit:
                break
                
    except Exception as e:
        print(f"[ERROR] RTS fetch error: {e}")
    
    return items


def fetch_random_quote(language=DEFAULT_LANGUAGE):
    """
    Fetch a random quote from ZenQuotes API and translate if needed.
    Falls back to predefined list if the API fails.
    """
    try:
        # First try the ZenQuotes API
        response = requests.get(ZENQUOTES_API_URL, timeout=5)
        response.raise_for_status()
        quote_data = response.json()[0]  # API returns array with single quote
        
        # If not in target language, translate it
        if language.lower() != "english":
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "system",
                    "content": f"You are a professional translator specializing in literary and philosophical texts. Translate this quote to {language}, maintaining its poetic and impactful nature while ensuring it sounds natural."
                },
                {
                    "role": "user",
                    "content": f'Translate this quote and author name with elegance: "{quote_data["q"]}" - {quote_data["a"]}'
                }],
                temperature=0.7
            )
            translated = response.choices[0].message.content.strip()
            
            # Split the translation back into quote and author
            if " - " in translated:
                quote, author = translated.rsplit(" - ", 1)
            else:
                quote = translated
                author = quote_data["a"]
            
            return {
                "quote": quote.strip('"'),
                "author": author
            }
        else:
            return {
                "quote": quote_data["q"],
                "author": quote_data["a"]
            }
            
    except Exception as e:
        print(f"[INFO] Using fallback quote system: {str(e)}")
        # Use fallback quotes if API fails
        return random.choice(FALLBACK_QUOTES)

def fetch_daily_boost(language=DEFAULT_LANGUAGE):
    """
    Generate daily affirmations and motivation using AI.
    Returns a dictionary with different types of motivational content.
    """
    boost_content = {
        "affirmation": random.choice(FALLBACK_AFFIRMATIONS),
        "motivation": "",
        "goal": ""
    }
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Generate a motivational quote using AI
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": f"""You are a wise philosopher and motivational speaker who creates impactful quotes in {language}.
                Create a profound and original quote that feels timeless.
                The quote should be inspiring and thought-provoking.
                Include a fictional but plausible author name that sounds authentic.
                Format: "quote" - Author Name"""
            },
            {
                "role": "user",
                "content": f"Create an original motivational quote about {random.choice(['success', 'perseverance', 'growth', 'wisdom', 'courage', 'creativity', 'happiness', 'inner peace'])}"
            }],
            temperature=0.9
        )
        boost_content["motivation"] = response.choices[0].message.content.strip()
        
        # Generate a personalized goal/intention
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "system",
                "content": f"""You are a life coach who creates personalized, actionable daily intentions in {language}.
                Create a powerful, specific intention that inspires action.
                Keep it short (1-2 sentences), positive, and impactful.
                Make it feel personal and immediate."""
            },
            {
                "role": "user",
                "content": "Create a powerful daily intention that encourages personal growth and positive action."
            }],
            temperature=0.8
        )
        boost_content["goal"] = response.choices[0].message.content.strip()
        
    except Exception as e:
        print(f"[WARN] Could not generate some motivation content: {e}")
    
    return boost_content


# ------------------------------------------------------
# PDF GENERATION
# ------------------------------------------------------
# Create a shared canvas class for both test and main documents
class PageCountCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._current_page = 1  # Start at 1 instead of 0

    def showPage(self):
        canvas.Canvas.showPage(self)
        self._current_page += 1  # Increment after showing the page

    def save(self):
        canvas.Canvas.save(self)

def calculate_content_size(doc, content, styles):
    """
    Calculate the approximate size of content with current styles.
    Returns the number of pages it would take.
    """
    from reportlab.platypus.doctemplate import FrameBreak, PageBreak
    from reportlab.platypus.paragraph import Paragraph
    
    # Create a temporary document to measure content
    class SizeDocTemplate(BaseDocTemplate):
        def __init__(self):
            super().__init__("size_test.pdf", pagesize=A4)
            self.page_count = 0
            
        def handle_pageBegin(self):
            self.page_count += 1
            super().handle_pageBegin()

        def handle_pageEnd(self):
            super().handle_pageEnd()
    
    doc_test = SizeDocTemplate()
    doc_test.addPageTemplates(doc.pageTemplates)
    
    # Build flowables with current styles
    flowables = []
    current_section = None
    
    for text in content:
        if not text.strip():
            continue
            
        has_emoji = any(ord(char) > 0x1F300 for char in text)
        style_to_use = styles["emoji_style"] if has_emoji else styles["article_style"]
            
        if text.isupper() and "-" in text:
            if text == "CITATION DU JOUR":
                flowables.append(Paragraph(text, styles["quote_section_style"]))
            else:
                flowables.append(Paragraph(text, styles["section_header_style"]))
            current_section = text
        elif current_section == "CITATION DU JOUR":
            if text.startswith("❝") or text.startswith("«"):
                flowables.append(Paragraph(text, styles["quote_style"]))
            elif text.startswith("—") or text.startswith("-"):
                flowables.append(Paragraph(text, styles["attribution_style"]))
        elif text.strip().split('.')[0].isdigit():
            flowables.append(Paragraph(text, styles["article_title_style"]))
        else:
            flowables.append(Paragraph(text, style_to_use))
    
    # Add a spacer at the end to ensure content fills all pages
    flowables.append(Spacer(1, 1))
    
    # Build document to count pages
    doc_test.build(flowables, canvasmaker=PageCountCanvas)
    return doc_test.page_count

def build_newspaper_pdf(pdf_filename, story_content, target_pages=2):
    """
    Generate a multi-column PDF (A4) with an old-school newspaper style.
    Dynamically adjusts font sizes to fit content within the specified number of pages.
    """
    page_width, page_height = A4
    
    # Convert 5mm to points (reportlab uses points)
    margin = 0.5 * cm  # 5mm = 0.5cm
    footer_height = 1 * cm  # Height for the footer
    
    class NumberedDocTemplate(BaseDocTemplate):
        def __init__(self, *args, **kwargs):
            BaseDocTemplate.__init__(self, *args, **kwargs)
            self.current_page = 0

        def handle_pageBegin(self):
            self.current_page += 1
            super().handle_pageBegin()
    
    doc = NumberedDocTemplate(
        pdf_filename,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin + footer_height,  # Add space for footer
    )
    
    def footer(canvas, doc):
        canvas.saveState()
        # Get current date
        try:
            date_str = format_date(datetime.datetime.now(), format="MMMM dd, yyyy", locale='en')
        except:
            date_str = datetime.datetime.now().strftime("%d/%m/%Y")
            
        footer_text = f"The Garden Report - {date_str} - Page {canvas._current_page} of {target_pages}"
        canvas.setFont("Courier", 8)
        canvas.drawCentredString(page_width/2, margin/2, footer_text)
        canvas.restoreState()
    
    gutter = 0.5 * cm  # Reduced gutter to match smaller margins
    column_width = (page_width - 2 * margin - 2 * gutter) / 3
    
    # Define frames for the content
    content_frames = [
        Frame(
            doc.leftMargin + i * (column_width + gutter),
            doc.bottomMargin,
            column_width,
            page_height - doc.topMargin - doc.bottomMargin,
            leftPadding=0,
            bottomPadding=0,
            rightPadding=0,
            topPadding=0,
            showBoundary=0
        )
        for i in range(3)
    ]
    
    # Create page template with footer
    page_template = PageTemplate(
        id="ThreeColumns",
        frames=content_frames,
        onPage=footer
    )
    doc.addPageTemplates([page_template])
    
    styles = getSampleStyleSheet()
    
    # Define initial styles with default sizes
    style_definitions = {
        "masthead_style": ParagraphStyle(
            "Masthead",
            parent=styles["Title"],
            fontName="Courier-Bold",
            fontSize=32,
            leading=36,
            alignment=1,
            textColor=colors.black,
            spaceAfter=6
        ),
        "subtitle_style": ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontName="Courier-Oblique",
            fontSize=12,
            leading=14,
            alignment=1,
            textColor=colors.black,
            spaceBefore=0,
            spaceAfter=20
        ),
        "section_header_style": ParagraphStyle(
            "SectionHeader",
            parent=styles["Heading1"],
            fontName="Courier-Bold",
            fontSize=18,  # Increased base size
            leading=22,   # Increased leading
            alignment=0,
            textColor=colors.black,
            spaceBefore=20,
            spaceAfter=12,
            borderWidth=1,  # Add border
            borderColor=colors.black,
            borderPadding=5,
        ),
        "article_title_style": ParagraphStyle(
            "ArticleTitle",
            parent=styles["Heading2"],
            fontName="Courier-Bold",
            fontSize=16,
            leading=16,
            alignment=0,
            textColor=colors.black,
            spaceBefore=12,
            spaceAfter=8,
            leftIndent=10,
            rightIndent=10,
        ),
        "article_style": ParagraphStyle(
            "Article",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=12,
            leading=11,
            alignment=4,
            firstLineIndent=15,
            spaceBefore=0,
            spaceAfter=8
        ),
        "quote_section_style": ParagraphStyle(
            "QuoteSection",
            parent=styles["Heading1"],
            fontName="Courier-Bold",
            fontSize=18,  # Match section_header_style
            leading=22,   # Match section_header_style
            alignment=1,
            textColor=colors.black,
            spaceBefore=20,
            spaceAfter=12,
            borderWidth=1,  # Add border
            borderColor=colors.black,
            borderPadding=5,
        ),
        "quote_style": ParagraphStyle(
            "Quote",
            parent=styles["Normal"],
            fontName="Courier-Oblique",
            fontSize=14,
            leading=18,
            alignment=1,
            textColor=colors.black,
            leftIndent=30,
            rightIndent=30,
            spaceBefore=0,
            spaceAfter=10
        ),
        "attribution_style": ParagraphStyle(
            "Attribution",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=12,
            leading=14,
            alignment=1,
            textColor=colors.black,
            spaceBefore=0,
            spaceAfter=20
        ),
        "emoji_style": ParagraphStyle(
            "EmojiText",
            parent=styles["Normal"],
            fontName="EmojiFont",
            fontSize=12,
            leading=14,
            alignment=0,
            textColor=colors.black
        )
    }
    
    # Calculate initial content size
    num_pages = calculate_content_size(doc, story_content, style_definitions)
    
    # If content exceeds target_pages or is too short, adjust font sizes
    if num_pages != target_pages:
        scale_factor = target_pages / num_pages
        
        # Limit the scaling to reasonable bounds
        scale_factor = max(0.7, min(1.3, scale_factor))
        
        # Adjust font sizes and leading proportionally while preserving hierarchy
        base_font_size = style_definitions["article_style"].fontSize
        base_leading = style_definitions["article_style"].leading
        
        for style_name, style in style_definitions.items():
            # Calculate relative size compared to base
            relative_size = style.fontSize / base_font_size
            relative_leading = style.leading / base_leading
            
            # Apply scaling while maintaining relative sizes
            style.fontSize = max(6, int(base_font_size * scale_factor * relative_size))
            style.leading = max(8, int(base_leading * scale_factor * relative_leading))
            
            # Scale spacing proportionally
            if hasattr(style, 'spaceBefore'):
                style.spaceBefore = int(style.spaceBefore * scale_factor)
            if hasattr(style, 'spaceAfter'):
                style.spaceAfter = int(style.spaceAfter * scale_factor)
            if hasattr(style, 'firstLineIndent'):
                style.firstLineIndent = int(style.firstLineIndent * scale_factor)
            if hasattr(style, 'borderPadding'):
                style.borderPadding = int(style.borderPadding * scale_factor)
    
    # Build flowables with adjusted styles
    flowables = []
    
    # Add masthead
    try:
        date_str = format_date(datetime.datetime.now(), format="EEEE MMMM dd, yyyy", locale='en')
    except:
        date_str = datetime.datetime.now().strftime("%A %d %B %Y")
    flowables.append(Paragraph("The Garden Report", style_definitions["masthead_style"]))
    flowables.append(Paragraph(date_str, style_definitions["subtitle_style"]))
    
    # Process content with appropriate styles
    current_section = None
    
    for text in story_content:
        if not text.strip():
            continue
            
        has_emoji = any(ord(char) > 0x1F300 for char in text)
        style_to_use = style_definitions["emoji_style"] if has_emoji else style_definitions["article_style"]
            
        if text.isupper() and "-" in text:
            if text == "CITATION DU JOUR":
                flowables.append(Paragraph(text, style_definitions["quote_section_style"]))
            else:
                flowables.append(Paragraph(text, style_definitions["section_header_style"]))
            current_section = text
        elif current_section == "CITATION DU JOUR":
            if text.startswith("❝") or text.startswith("«"):
                flowables.append(Paragraph(text, style_definitions["quote_style"]))
            elif text.startswith("—") or text.startswith("-"):
                flowables.append(Paragraph(text, style_definitions["attribution_style"]))
        elif text.strip().split('.')[0].isdigit():  # Check if starts with any number followed by a period
            # If it's a Rosary prayer (e.g., '1. Annunciation'), use article_style, not article_title_style
            flowables.append(Paragraph(text, style_definitions["article_style_small"]))
        else:
            flowables.append(Paragraph(text, style_to_use))
    
    # Add a spacer at the end to ensure content fills all pages
    flowables.append(Spacer(1, 1))
    
    # Build the PDF with our custom canvas
    doc.build(flowables, canvasmaker=PageCountCanvas)

def print_pdf(pdf_filename, printer_name=""):
    """Print the PDF file using the 'lpr' command."""
    if not os.path.exists(pdf_filename):
        print(f"[ERROR] PDF file not found: {pdf_filename}")
        return

    print_cmd = ["lpr", pdf_filename]
    if printer_name:
        print_cmd = ["lpr", "-P", printer_name, pdf_filename]

    try:
        subprocess.run(print_cmd, check=True)
        print(f"Sent {pdf_filename} to printer '{printer_name or 'default'}'.")
    except Exception as e:
        print(f"[ERROR] Printing file: {e}")

# ------------------------------------------------------
# MAIN
# ------------------------------------------------------
def main(use_cache=False, auto_print=False, articles_per_source=None, target_pages=2):
    """
    Main function to generate the Garden Report.
    :param use_cache: If True, use cached content if available
    :param auto_print: If True, automatically print to default printer
    :param articles_per_source: Number of articles to fetch per source (overrides MAX_ITEMS)
    :param target_pages: Number of pages to generate (default: 2)
    """
    # Create press directory if it doesn't exist
    os.makedirs("press", exist_ok=True)

    # Set number of articles to fetch
    num_articles = articles_per_source if articles_per_source is not None else MAX_ITEMS

    # Generate unique filename with timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_filename = f"press/{PDF_PREFIX}_{timestamp}.pdf"

    # Try to load from cache if use_cache is True
    content = None
    if use_cache:
        content = load_from_cache()
        if content:
            print("Using cached content...")
    
    # If no cache or cache disabled, fetch fresh content
    if content is None:
        content = []
        
        # Add weather
        weather_info = fetch_weather(WEATHER_URL)
        content.append(weather_info)
        content.append("")  # Add spacing

        # Add Rosary of the day
        print("Fetching rosary of the day...")
        rosary_data = fetch_rosary(DEFAULT_LANGUAGE)
        if rosary_data:
            content.append("Daily Rosary")
            content.append(SECTION_SEPARATOR)
            content.append(rosary_data["name"])
            for idx, prayer in enumerate(rosary_data["prayers"], 1):
                content.append(f"{idx}. {prayer}")
        
        # Fetch daily readings and reflections from USCCB Daily Readings
        print("Fetching daily readings and reflections from USCCB Daily Readings...")
        usccb_readings_data = fetch_usccb_readings(DEFAULT_LANGUAGE)
        if usccb_readings_data:
            content.append("USCCB Daily Readings")
            content.append(SECTION_SEPARATOR)
            content.append(usccb_readings_data["readings"])


        # Fetch and process Eagle Country news
        # print("Fetching Eagle Country news...")
        # eagle_country_news = fetch_rss_headlines_with_details(EAGLE_COUNTRY_URL, num_articles, DEFAULT_LANGUAGE)
        
        # if eagle_country_news:
        #     content.append("Local News - Top Stories")
        #     content.append(SECTION_SEPARATOR)
        #     for idx, item in enumerate(eagle_country_news, 1):
        #         content.append(f"{idx}. {item['title']}")
        #         if item.get('content'):
        #             content.append("")
        #             content.append(item['content'])
        #         content.append("")
        

        # Fetch and process Hacker News stories
        # print("Fetching Hacker News stories...")
        # hn_news = fetch_hackernews_top_stories(num_articles, DEFAULT_LANGUAGE)
        
        # if hn_news:
        #     content.append("Hacker News - Top Stories")
        #     content.append(SECTION_SEPARATOR)
        #     for idx, item in enumerate(hn_news, 1):
        #         content.append(f"{idx}. {item['title']}")
        #         if item.get('content_summary'):
        #             content.append("")
        #             content.append(item['content_summary'])
        #         content.append("")
        
        # Add quote of the day
        # print("Fetching quote of the day...")
        # quote_data = fetch_random_quote(DEFAULT_LANGUAGE)
        # if quote_data:
        #     content.append("CITATION DU JOUR - TOP QUOTES")
        #     content.append(SECTION_SEPARATOR)
        #     content.append(f"« {quote_data['quote']} »")
        #     content.append(f"— {quote_data['author']}")
        
        # Add daily boost
        # print("Preparing daily boost...")
        # boost_data = fetch_daily_boost(DEFAULT_LANGUAGE)
        # if boost_data:
        #     content.append("BOOST DU JOUR - TOP MOTIVATION")
        #     content.append(SECTION_SEPARATOR)
        #     content.append("✧ Affirmation du jour:")
        #     content.append(boost_data["affirmation"])
        #     content.append("")
        #     if boost_data.get("motivation"):
        #         content.append("★ Pensée motivante:")
        #         content.append(boost_data["motivation"])
        #         content.append("")
        #     if boost_data.get("goal"):
        #         content.append("⟡ Intention du jour:")
        #         content.append(boost_data["goal"])
        #     content.append("")
        
        # Save to cache for future use
        save_to_cache(content)
    
    # Generate PDF
    build_newspaper_pdf(pdf_filename, content, target_pages)
    
    # Print if auto_print is True or printer name is configured
    if auto_print or PRINTER_NAME:
        print_pdf(pdf_filename, PRINTER_NAME)
    
    print(f"The Garden Report generated: {pdf_filename}")

    # Ask user if they want to open the PDF
    while True:
        response = input("Would you like to open the PDF? (y/n): ").lower().strip()
        if response in ['y', 'yes']:
            try:
                if sys.platform == "darwin":  # macOS
                    subprocess.run(["open", pdf_filename])
                elif sys.platform == "win32":  # Windows
                    os.startfile(pdf_filename)
                else:  # Linux/Unix
                    subprocess.run(["xdg-open", pdf_filename])
                break
            except Exception as e:
                print(f"Error opening PDF: {e}")
                break
        elif response in ['n', 'no']:
            break
        else:
            print("Please answer 'y' or 'n'")

# ------------------------------------------------------
if __name__ == "__main__":
    # Parse command line arguments
    use_cache = "--use-cache" in sys.argv
    auto_print = "--print" in sys.argv
    
    # Parse number of articles if provided
    articles_per_source = None
    target_pages = 2  # Default value
    
    for i, arg in enumerate(sys.argv):
        if arg == "--articles":
            try:
                articles_per_source = int(sys.argv[i + 1])
            except (IndexError, ValueError):
                print("[ERROR] --articles requires a number value")
                sys.exit(1)
        elif arg == "--pages":
            try:
                target_pages = int(sys.argv[i + 1])
                if target_pages < 1:
                    print("[ERROR] --pages must be at least 1")
                    sys.exit(1)
            except (IndexError, ValueError):
                print("[ERROR] --pages requires a number value")
                sys.exit(1)
    
    main(use_cache, auto_print, articles_per_source, target_pages)