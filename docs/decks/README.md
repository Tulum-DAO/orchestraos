# Build-a-thon decks — companion docs

One page per slide deck from the Tulum Build-a-thon (2026-09-19). Each page holds everything its deck claims, in the same order, with the file in this repo that proves each claim, the effect that was observed and when, and the known gaps stated without softening. Claims that were not verified against the code are marked `[UNVERIFIED]`.

These pages are written to be handed to an agent. Point yours at this folder and it has the whole picture.

| Deck | Page | One line |
|---|---|---|
| 1 | [What OrchestraOS is](1-what-it-is.md) | The four ideas, the problem, and how honest we are being. |
| 2 | [Seats, identity, and messaging](2-seats-identity-messaging.md) | A seat is a registered identity with a lineage; seats mail each other through a durable store. |
| 3 | [Rotation](3-rotation.md) | Two paths: the operator-run gated handoff with canary questions, and the automated blue-green swap with a log probe. Different guarantees. |
| 4 | [Every real decision reaches a human](4-decisions-reach-a-human.md) | File a card, stop, get resumed by the answer in your own session. |
| 5 | [Status and visibility](5-status-and-visibility.md) | Every surface must agree on what an agent is doing; today they do not, and here is why. |
| 6 | [Memory](6-memory.md) | Markdown on disk, one index loaded at boot, shared across generations by one line of sed. |

Reading order is 1 to 6. Rotation needs seats; decisions need messaging; status needs all three; memory stands alone.

Where a page names a file, it is a path in this repository at the commit that shipped it. Where it names an effect, the date is the day it was observed on the reference install.
