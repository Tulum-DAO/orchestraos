# Diagram set

Sources are Mermaid (`.mmd`); `python3 render.py` regenerates every `.svg` and `.png`
(needs `playwright` and a Chromium; set `CHROME_PATH` if the default is missing).

| File | Use |
|---|---|
| architecture-map | the one-page map from docs/ARCHITECTURE.md, with hooks and the facts store |
| rotation-sequence | blue-green rotation: observe, prewarm, handoff, readback, swap, retire |
| decision-to-phone | a worker's decision becoming a card on phone/watch/Telegram and the resume |
| gate-graphic | the seven-step gate from docs/GATE.md |
| track-cards | the eleven tracks branching off the gate |
| token-stat | build cost: ~80B tokens, API list price vs CLI subscription plans |
