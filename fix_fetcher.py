content = open('sec_fetcher.py').read()

old = '''def fetch_filing_xml(index_url: str) -> str:
    """Fetch the actual Form 4 XML from the filing index page."""
    try:
        # Get the index page to find the XML file
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        # Find the .xml filing link
        xml_match = re.search(r\'href="(/Archives/edgar/data/[^"]+\\.xml)"\', resp.text)
        if xml_match:
            xml_url = "https://www.sec.gov" + xml_match.group(1)
            time.sleep(0.2)  # SEC rate limit courtesy
            xml_resp = requests.get(xml_url, headers=HEADERS, timeout=15)
            xml_resp.raise_for_status()
            return xml_resp.text
        else:
            # Fall back to index page HTML
            return resp.text[:6000]

    except Exception as e:
        return f"Error fetching filing: {e}"'''

new = '''def fetch_filing_xml(index_url: str) -> str:
    """Fetch the actual Form 4 XML from the filing index page."""
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        xml_matches = re.findall(r\'href=