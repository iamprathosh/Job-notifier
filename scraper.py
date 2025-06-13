import os
import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urlparse, parse_qs
import time

# --- Configuration ---
PROCESSED_JOBS_FILE = "processed_jobs.json"

def get_processed_jobs():
    """Reads the list of processed job links from the JSON file."""
    try:
        with open(PROCESSED_JOBS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # If the file doesn't exist or is empty, start with an empty list
        return []

def save_processed_jobs(jobs_list):
    """Saves the updated list of processed jobs back to the JSON file."""
    with open(PROCESSED_JOBS_FILE, 'w') as f:
        json.dump(jobs_list, f, indent=2)

def analyze_with_gemini(job_details, api_key):
    """Analyzes job details using the Gemini API."""
    prompt = f"""
        Please act as an expert HR screener. Analyze the following job posting.
        My conditions are:
        1. The job MUST be for a "fresher", "new graduate", "2025 batch", or 0 years experience.
        2. The role MUST be for a "software developer", "software engineer", "programmer", "web developer", or similar coding role.
        3. The location MUST be "Bengaluru" (Bangalore).

        Job Details:
        - Title: {job_details.get('title', 'N/A')}
        - Source: {job_details.get('company', 'N/A')}
        - Page Content (first 2000 chars): {job_details.get('details', 'N/A')[:2000]}

        Based ONLY on this text, does this job meet ALL my conditions (1, 2, and 3)?
        Respond with a single word: YES or NO.
    """
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    
    try:
        response = requests.post(api_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=30)
        response.raise_for_status()
        result = response.json()
        text_response = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
        print(f"AI Analysis for '{job_details.get('title')}': {text_response.strip()}")
        return "YES" in text_response.upper()
    except requests.exceptions.RequestException as e:
        print(f"Error calling Gemini API: {e}")
        return False

def send_ntfy_notification(job, ntfy_topic, is_no_job_notification=False):
    """Sends a push notification using ntfy.sh."""
    
    if is_no_job_notification:
        # Prepare a special notification for when no new jobs are found
        notification_title = job['title']
        notification_body = "No new relevant fresher jobs found in the last run."
        notification_tags = "search,x"
    else:
        # Prepare the standard notification for a found job
        notification_title = "New Fresher Job Found!"
        notification_body = job['title'].encode('utf-8')
        notification_tags = "briefcase"

    try:
        requests.post(
            f"https://ntfy.sh/{ntfy_topic}",
            data=notification_body,
            headers={
                "Title": notification_title,
                "Click": job['link'],
                "Tags": notification_tags
            },
            timeout=20
        )
        print(f"Successfully sent notification: {notification_title}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending ntfy notification: {e}")


def main():
    # --- Get secrets from GitHub Actions environment ---
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
    # The GITHUB_REPOSITORY variable is automatically provided by GitHub Actions
    GITHUB_REPOSITORY_URL = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}"


    if not GEMINI_API_KEY or not NTFY_TOPIC:
        print("Error: Missing GEMINI_API_KEY or NTFY_TOPIC environment variables.")
        return

    # --- Load existing data and prepare search ---
    processed_jobs = get_processed_jobs()
    print(f"Loaded {len(processed_jobs)} previously processed jobs.")
    
    search_query = '"walk-in interview" AND ("fresher" OR "2025 batch") AND "developer" in Bangalore'
    # The Google search parameter 'tbs=qdr:w' searches for results from the past week.
    google_url = f"https://www.google.com/search?q={search_query}&tbs=qdr:w"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'}
    
    print(f"Searching Google: {google_url}")
    response = requests.get(google_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    # --- Find all valid links on the Google search results page ---
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('/url?q='):
            url = parse_qs(urlparse(href).query).get('q', [None])[0]
            if url and 'google.com' not in url and url not in processed_jobs:
                links.append(url)

    if not links:
        print("No new job links found on Google search.")
    else:
        print(f"Found {len(links)} new, unprocessed links to check.")

    new_jobs_found = []

    # --- Process each new link ---
    for link in links:
        try:
            print(f"Processing link: {link}")
            time.sleep(1) # Be polite to servers
            page_response = requests.get(link, headers=headers, timeout=20)
            page_soup = BeautifulSoup(page_response.text, 'html.parser')
            
            job_details = {
                "title": page_soup.title.string if page_soup.title else "No Title Found",
                "company": urlparse(link).hostname,
                "details": " ".join(page_soup.body.get_text().split()),
                "link": link
            }

            if analyze_with_gemini(job_details, GEMINI_API_KEY):
                send_ntfy_notification(job_details, NTFY_TOPIC)
                new_jobs_found.append(link)

        except Exception as e:
            print(f"Could not process link {link}. Error: {e}")

    # --- Final Step: Update processed jobs file OR send a "no jobs found" notification ---
    if new_jobs_found:
        updated_processed_jobs = processed_jobs + new_jobs_found
        save_processed_jobs(updated_processed_jobs)
        print(f"Finished. Added {len(new_jobs_found)} new jobs to processed list.")
    else:
        print("No new relevant jobs were found in this run. Sending a status notification.")
        # Create a dummy job object for the notification function
        status_update = {
            'title': 'Job Search Complete',
            'link': f"{GITHUB_REPOSITORY_URL}/actions" # Link to the actions page
        }
        send_ntfy_notification(status_update, NTFY_TOPIC, is_no_job_notification=True)


if __name__ == "__main__":
    main()
