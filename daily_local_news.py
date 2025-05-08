#!/usr/bin/env python3

"""
The Garden Report: Gather prayers and daily readings, 
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

# RSS Feeds and News Sites
EAGLE_COUNTRY_URL = "https://www.eaglecountryonline.com/news/local-news/feed.xml"

# Weather: Open-Meteo API
CITY_NAME = "Lawrenceburg"  # City name for display purposes
CITY_LAT = 39.0909  # city latitude
CITY_LON = -84.85  # city longitude
WEATHER_URL = (
    f"https://api.open-meteo.com/v1/forecast?"
    f"latitude={CITY_LAT}&longitude={CITY_LON}&timezone=America%2FNew_York"
    f"&forecast_days=1&daily=temperature_2m_max,sunset,sunrise,precipitation_sum&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch&hourly=temperature_2m,weather_code,precipitation_probability,precipitation"
)


# (Optional) OpenAI Summarization
USE_OPENAI_SUMMARY = False
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Get from environment variable

# Printer Name (for 'lpr')
PRINTER_NAME = ""  # e.g., "EPSON_XXXX" or leave blank for default

# PDF output filename prefix
PDF_PREFIX = "the_garden_report_daily_prayers"

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
    Fetch weather data from Open-Meteo API, returning a list of dictionaries with weather details.
    """

    # WMO Weather interpretation codes (https://open-meteo.com/en/docs)
    weather_descriptions = {
        0: "☀️ Clear sky",
        1: "🌤️ Mainly clear", 2: "⛅ Partly cloudy", 3: "☁️ Overcast",
        45: "🌫️ Foggy", 48: "🌫️ Depositing rime fog",
        51: "🌦️ Light drizzle", 53: "🌦️ Moderate drizzle", 55: "🌧️ Dense drizzle",
        61: "🌧️ Slight rain", 63: "🌧️ Moderate rain", 65: "🌧️ Heavy rain",
        71: "❄️ Slight snow", 73: "❄️ Moderate snow", 75: "❄️ Heavy snow",
        77: "🌨️ Snow grains",
        80: "🌦️ Slight rain showers", 81: "🌦️ Moderate rain showers", 82: "⛈️ Violent rain showers",
        85: "🌨️ Slight snow showers", 86: "🌨️ Heavy snow showers",
        95: "⛈️ Thunderstorm", 96: "⛈️ Thunderstorm with hail", 99: "⛈️ Thunderstorm with heavy hail"
    }

    items = []
    try:
        resp = requests.get(city_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if "daily" in data:
            sunrise = datetime.datetime.strptime(data["daily"]["sunrise"][0], "%Y-%m-%dT%H:%M").strftime("%I:%M %p")
            sunset = datetime.datetime.strptime(data["daily"]["sunset"][0], "%Y-%m-%dT%H:%M").strftime("%I:%M %p")

            items.append({
                "title": "Sunrise / Sunset",
                "content": f"🌅 {sunrise} / 🌇 {sunset}"
            })

        if "hourly" in data:
            for i, time in enumerate(data["hourly"]["time"]):
                temperature = data["hourly"]["temperature_2m"][i]
                weather_code = data["hourly"]["weather_code"][i]
                precipitation_probability = data["hourly"]["precipitation_probability"][i]
                precipitation = data["hourly"]["precipitation"][i]
                time_formatted = datetime.datetime.strptime(time, "%Y-%m-%dT%H:%M").strftime("%I:%M %p")

                weather_description = weather_descriptions.get(weather_code, "Unknown conditions")
                if time_formatted in ["06:00 AM", "08:00 AM", "10:00 AM", "12:00 PM", "02:00 PM", "04:00 PM", "06:00 PM", "08:00 PM", "10:00 PM"]:
                    items.append({
                        "title": time_formatted,
                        "content": (
                            f"{temperature}°F - {weather_description}\n"
                            f"{precipitation}in / {precipitation_probability}% chance\n"
                        )
                    })
    except Exception as e:
        print(f"[ERROR] Weather fetch: {e}")
        return []  # Return an empty list on error
    return items



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
        if isinstance(text, tuple):
            style_name, item = text
            if not item.strip():
                continue

            if isinstance(item, str):
                has_emoji = any(ord(char) > 0x1F300 for char in item)
            else:
                has_emoji = False

            style_to_use = styles["emoji_style"] if has_emoji else styles["article_style"]

            style = styles.get(style_name, styles["article_style"])
            flowables.append(Paragraph(item, style))
        else:
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
                flowables.append(Paragraph(text, styles["article_style_small"]))
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
            
        footer_text = f"The Garden Report: Daily Prayers & Readings for {date_str}"
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
            fontSize=16,  # Increased base size
            leading=20,   # Increased leading
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
            fontSize=12,
            leading=14,
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
            leading=14,
            alignment=4,
            firstLineIndent=15,
            spaceBefore=0,
            spaceAfter=8
        ),
        "article_style_no_indent": ParagraphStyle(
            "Article",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=12,
            leading=14,
            alignment=4,
            firstLineIndent=0,
            spaceBefore=0,
            spaceAfter=8
        ),
        "article_style_small": ParagraphStyle(
            "Article",
            parent=styles["Normal"],
            fontName="Courier",
            fontSize=10,
            leading=10,
            alignment=4,
            firstLineIndent=0,
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
    
    for item in story_content:
        if isinstance(item, tuple):
            style_name, text = item
            if not text.strip():
                continue

            if isinstance(text, str):
                has_emoji = any(ord(char) > 0x1F300 for char in text)
            else:
                has_emoji = False

            style_to_use = style_definitions["emoji_style"] if has_emoji else style_definitions["article_style"]
            
            style = style_definitions.get(style_name, style_definitions["article_style"])
            flowables.append(Paragraph(text, style))
        else:
            text = item
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
                flowables.append(Paragraph(text, style_definitions["article_style"]))
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

    if sys.platform == "win32":
        print("Printing PDF using os.startfile ...")
        os.startfile(os.path.abspath(pdf_filename), "print")


# ------------------------------------------------------
# MAIN
# ------------------------------------------------------
def main(use_cache=False, auto_print=False, articles_per_source=None, target_pages=1):
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
        print("Fetching weather...")
        if weather_info:
            print("Printing weather ...")
            content.append(("section_header_style", "Daily Weather"))
            for idx, item in enumerate(weather_info, 1):
                content.append(("article_style_no_indent", item['title']))
                if item.get('content'):
                    content.append(("article_style_small", item['content']))
                content.append("")



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
        

        # Save to cache for future use
        save_to_cache(content)
    
    # Generate PDF
    build_newspaper_pdf(pdf_filename, content, target_pages)
    
    # Print if auto_print is True or printer name is configured
    if auto_print or PRINTER_NAME:
        print_pdf(pdf_filename, PRINTER_NAME)
    
    print(f"The Garden Report generated: {pdf_filename}")


# ------------------------------------------------------
if __name__ == "__main__":
    # Parse command line arguments
    use_cache = "--use-cache" in sys.argv
    auto_print = "--print" in sys.argv
    
    # Parse number of articles if provided
    articles_per_source = None
    target_pages = 1  # Default value
    
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