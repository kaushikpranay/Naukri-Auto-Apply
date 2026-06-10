import asyncio
from playwright.async_api import async_playwright
from app.utils.config_loader import load_settings, resolve_path

async def debug_drawer(page):
    drawer = page.locator('.chatbot_Drawer')
    count = await drawer.count()
    print(f"Drawer count: {count}")
    
    if count > 0:
        visible = await drawer.is_visible()
        print(f"Drawer visible: {visible}")
        
        text = await drawer.inner_text()
        print(f"Drawer text length: {len(text)}")
        print(f"Drawer text preview:\n{text}")
        
        radios = await drawer.locator("[role='radio']").count()
        print(f"Radio buttons: {radios}")
        
        editable = await drawer.locator("[contenteditable='true']").count()
        print(f"Contenteditable fields: {editable}")
        
        inputs = await drawer.locator("input:not([type='hidden']), textarea").count()
        print(f"Text inputs/textareas: {inputs}")
    else:
        print("No drawer found in DOM.")

async def run():
    settings = load_settings()
    profile_path = resolve_path(settings.browser.profile_dir)
    
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            viewport={"width": 1280, "height": 720},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        url = "https://www.naukri.com/job-listings-genai-devops-engineer-pwc-service-delivery-center-kolkata-hyderabad-bengaluru-4-to-8-years-210526027509"
        print(f"Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        
        print("\n--- Initial State ---")
        await debug_drawer(page)
        
        # Click the exact Apply button
        apply_btn = page.locator("button#apply-button").first
        if await apply_btn.count() > 0:
            print("\nFound button#apply-button. Clicking it...")
            await apply_btn.click()
            # Wait up to 10 seconds for the chatbot drawer to appear
            for wait_sec in range(1, 11):
                await page.wait_for_timeout(1000)
                drawer_count = await page.locator('.chatbot_Drawer').count()
                if drawer_count > 0:
                    print(f"Drawer appeared after {wait_sec} seconds.")
                    break
            
            print("\n--- Post-Click State ---")
            await debug_drawer(page)
        else:
            print("\nCould not find button#apply-button")
            
        await context.close()

if __name__ == "__main__":
    asyncio.run(run())
