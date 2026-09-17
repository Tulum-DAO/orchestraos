"""Render every .mmd in this folder to .svg and .png (playwright + mermaid CDN)."""
import asyncio, pathlib, json
from playwright.async_api import async_playwright
HERE = pathlib.Path(__file__).parent
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=__import__('os').environ.get('CHROME_PATH') or None)
        pg = await b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        await pg.set_content('<html><body style="margin:0;background:#fff"><div id="d"></div>'
            '<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script></body></html>')
        await pg.wait_for_function("window.mermaid !== undefined")
        await pg.evaluate("mermaid.initialize({startOnLoad:false, theme:'neutral'})")
        for f in sorted(HERE.glob("*.mmd")):
            src = f.read_text()
            svg = await pg.evaluate("async s => (await mermaid.render('x'+Date.now(), s)).svg", src)
            f.with_suffix(".svg").write_text(svg)
            await pg.evaluate("s => document.getElementById('d').innerHTML = s", svg)
            el = await pg.query_selector("#d svg")
            await el.screenshot(path=str(f.with_suffix(".png")))
            print("rendered", f.name)
        await b.close()
asyncio.run(main())
