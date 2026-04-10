import requests
from bs4 import BeautifulSoup

def recon_dongqiudi():
    url = 'https://www.dongqiudi.com/china/csl/standings'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.dongqiudi.com/'
    }
    
    print(f"🚀 Starting Recon on: {url}")
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        print(f"✅ HTTP Status: {response.status_code}")
        
        if response.status_code != 200:
            print("❌ Failed to fetch page.")
            return

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Check Title
        print(f"📄 Page Title: {soup.title.string if soup.title else 'No title'}")
        
        # 2. Search for scripts containing data
        scripts = soup.find_all('script')
        found_data = False
        
        with open('csl_project_v2/data/recon_results.txt', 'w', encoding='utf-8') as f:
            f.write(f"Recon Results for {url}\n")
            f.write("="*50 + "\n\n")
            
            for i, s in enumerate(scripts):
                content = s.string if s.string else ""
                if content and ('standings' in content.lower() or 'data' in content.append_dict if hasattr(content, 'append_dict') else 'data' in content.lower()):
                    print(f"🔍 Found potential data in <script> tag #{i}")
                    f.write(f"--- Found potential data in <script> tag #{i} ---\n")
                    f.write(content[:5000] + "\n\n") # Limit to 5000 chars to avoid bloat
                    found_data = True
            
            if not found_data:
                print("⚠️ No obvious JSON/Data found in <script> tags. Checking for common table structures...")
                f.write("⚠️ No obvious JSON/Data found in <script> tags.\n\n")
                
                # Check for tables
                tables = soup.find_all('table')
                f.write(f"Found {len(tables)} <table> elements in the page.\n")
                if tables:
                    f.write("First table snippet (first 500 chars):\n")
                    f.write(str(tables[0])[:500] + "\n")
            else:
                print("✨ Success! Potential data snippets saved to recon_results.txt")

    except Exception as e:
        print(f"❌ Error during recon: {e}")

if __name__ == "__main__":
    recon_dongqiudi()
