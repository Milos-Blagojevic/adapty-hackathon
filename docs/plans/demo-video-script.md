# Resolve demo video: edit plan

Team 12, Adapty Hackathon Belgrade. Runs 5:06.

**Part 5 is cut to 316 words, about 1:55 at 165 wpm, by the owner's decision. Milos reads it before recording.**

| Part | Slot | Runtime |
|---|---|---|
| Title | 0:00–0:04 | 0:04 |
| 1. Before | 0:04–0:44 | 0:40 |
| 2. Our web app | 0:44–1:29 | 0:45 |
| 3. Phone | 1:29–2:19 | 0:50 |
| 4. Web again | 2:19–3:04 | 0:45 |
| 5. Milos | 3:04–5:00 | 1:56 |
| Closing | 5:00–5:06 | 0:06 |

`REC` marks a screen recording another agent is capturing. Overlays are in `stills/overlays/`, with a transparent background and a 1920×1080 frame. Put them on top of the clip.

## Title

| Time | Visual | Voice-over | Caption |
|---|---|---|---|
| 0:00–0:04 | `stills/00-title.png` | "Resolve. AI support for subscription apps, on Adapty." | on the card |

## Part 1. Before (0:40)

Mock frames have no on-screen note. The rating, inbox count and call list are made up and are not statistics. Complaint text is paraphrased from `kb/E-real-cases.md`.

| Time | Visual | Voice-over | Caption |
|---|---|---|---|
| 0:04–0:11 | `stills/p1-01-review.png` | "You run a subscription app. Someone gets charged after a free trial. So they write this." | on the card |
| 0:11–0:17 | `stills/p1-02-rating.png` | "One star, on Google Play or the App Store. And your rating drops." | on the card |
| 0:17–0:24 | `stills/p1-03-dead-end.png` | "The ones who do look for help find 'Contact support'. It goes nowhere." | on the card |
| 0:24–0:28 | `stills/p1-04-inbox.png` | "So they email. Then they email again." | on the card |
| 0:28–0:31 | `stills/p1-05-calls.png` | "Some even call." | on the card |
| 0:31–0:37 | `stills/p1-06-1in6.png` | "We read 21,825 App Store reviews. One in six angry reviews is about money." | on the card |
| 0:37–0:44 | `stills/p1-07-2of353.png` | "353 people described contacting support. Two got help." | on the card |

## Part 2. Our web app (0:45)

Each page gets a 6 s clip with its `lt-page-*` overlay. If you prefer full-screen cards, `stills/p2-01…07` have the same sentences, but they eat into the clip time.

| Time | Visual | Voice-over | Overlay |
|---|---|---|---|
| 0:44–0:47 | `stills/p2-00-section.png` | "This is what the support person gets." | |
| 0:47–0:53 | **REC web-1** Incoming | "Incoming. Every ticket in one list, already triaged." | `lt-page-incoming` |
| 0:53–0:59 | **REC web-2** Ticket | "A ticket. The cause, two or three options, a ready reply, the risk, the evidence." | `lt-page-ticket` |
| 0:59–1:05 | **REC web-3** Chat | "Chat. Talk to Level 2 as any user, and see what the code allowed." | `lt-page-chat` |
| 1:05–1:11 | **REC web-4** Agent settings | "Agent settings. The owner sets tools, limits, tone, and what always goes to a person." | `lt-page-settings` |
| 1:11–1:17 | **REC web-5** Analyze | "Analyze. The agent and people compared, reply by reply." | `lt-page-analyze` |
| 1:17–1:23 | **REC web-6** Insights | "Insights. Complaints grouped by paywall, with a draft A/B test." | `lt-page-insights` |
| 1:23–1:29 | **REC web-7** Impact | "Impact. What the problem costs. Milos comes back to this." | `lt-page-impact` |

## Part 3. Phone (0:50)

| Time | Visual | Voice-over | Overlay |
|---|---|---|---|
| 1:29–1:31 | `stills/p3-00-section.png` | | |
| 1:31–1:38 | **REC phone-happy**, opening | "This is FitBuddy, our demo app. Its users are seeded Adapty profiles." | `lt-demo-app` |
| 1:38–1:51 | **REC phone-happy**, chat | "A paying user is locked out. Level 2 reads the live profile and gives free days. The limit is checked in code. No ticket." | `lt-happy` |
| 1:51–2:07 | **REC phone-handoff**, Russian | "Now a harder one. This user writes in Russian and wants their money back. Level 2 answers in Russian." | `lt-russian` |
| 2:07–2:19 | **REC phone-handoff**, handoff | "But a refund is money risk. So it doesn't decide. It hands off to a person." | `lt-handoff` |

## Part 4. Web again (0:45)

The translation moment is the selling point, so it gets the longest shot in this part.

| Time | Visual | Voice-over | Overlay |
|---|---|---|---|
| 2:19–2:21 | `stills/p4-00-section.png` | | |
| 2:21–2:27 | **REC web-ticket**, Incoming | "The ticket is already here, triaged, with the whole chat." | |
| 2:27–2:41 | **REC web-ticket**, Ticket in English | "The user wrote in Russian. The manager reads English: the message and every reply option. Translated from Russian. Sends in Russian." | `lt-translate` |
| 2:41–2:45 | `stills/p4-translate.png` (optional recap, or stay on the clip) | | on the card |
| 2:45–2:49 | **REC web-ticket**, accept | "One click decides." | `lt-one-click` |
| 2:49–2:54 | **REC web-ticket**, phone | "And the user gets the reply in Russian." | |
| 2:54–2:57 | **REC web-ticket**, counters + Analyze | "The counters move. Analyze counts it." | `lt-counters` |
| 2:57–3:04 | **REC web-insights** | "And Insights: 12 of 16 trial complaints came from one onboarding flow. So it drafts an A/B test." | `lt-insights` |

## Part 5. Milos (his text, verbatim)

Timings are from his run sheet. "Spoken" is the word count at 165 wpm.

| Time | Visual | Voice-over (Milos) | Sheet / spoken |
|---|---|---|---|
| 3:04–3:06 | `stills/p5-00-section.png` | | taken from Stop 1's slot |
| to 3:29 | **REC impact**, Stop 1, top of the page | "This isn't a niche problem. In over 168 thousand recent bad reviews of the top US apps, 27 percent are about billing, not the app. The number one specific complaint is a free trial that turned into a charge. And users forgive crashes more than silence: a review about support says 'I'm leaving' 13.6 percent of the time, a review about a crash 5.6." | 23 s |
| to 3:45 | **REC impact**, Stop 2, dataset table (overlay `lt-lumen`) | "On FitBuddy, the app you just saw, with the organisers' data behind it: 9,722 payers, 112k a month, 3,200 a month refunded. About 1,170 refund, billing and trial events a month. If one in four writes in, that's 300 tickets, 35 hours of the founder's month at 7 minutes each." | 16 s |
| to 4:21 | **REC impact**, Stop 3, three cards | "The safe cases never become tickets: Level 2 answers in the app from the live profile and gives free days inside limits that are checked in code, not in the prompt. Everything with money risk goes to a person, triaged, 7 minutes become one click. On our 38-case eval that's 37 right categories and 29 right first options. And the part that scales with revenue: in our seeded set, 12 of 16 trial complaints came from one onboarding flow, and complaints like these become the A/B draft you just saw. The paywall gets fixed instead of the rating." | 36 s |
| to 4:50 | **REC impact**, Stop 4, Adapty section | "Adapty earns 1 percent of tracked revenue and already sells Refund Saver as 'Adapty pays for itself'. This is the same play one step earlier. On the 3 billion in Adapty's report that's roughly 100 million a year in refunds; a fifth prevented is 20 million kept by customers and about 200k for Adapty directly. Small directly, big as a reason to pick Adapty, in a market where 14,700 new subscription apps launch every month, almost none with a support team." | 29 s |
| to 5:00 | **REC impact**, Validate next section | "What we'd validate next: the share of Adapty's own support tickets that are end users asking about charges, a 200-ticket eval, and one real inbox for a week." | 10 s |

**Milos's don't-says:** "studies show X percent of all apps", "Adapty loses 100 million", or any number that isn't on the page. Say "recent negative reviews of top apps", not store-wide.

**Cut approved by the owner. Numbers are the page's own: 9,722 and about 1,170, not 9,700 and 1,200; 13.6% against 5.6% instead of 'two and a half times'. Needs Milos's read before he records. The eval numbers are the last single run on the merged code (37 and 29 of 38, before the brand change).**

## Closing

| Time | Visual | Voice-over | Caption |
|---|---|---|---|
| 5:00–5:06 | `stills/99-closing.png` | "Resolve. Safe cases answered in the app. Risky ones, one click." | on the card |

## Screen recordings: what must happen

All at 1920×1080, except the phone. Hold each state about 2 s longer than the slot so there is room to trim. Do not rely on any number in live data for the voice-over: the only on-screen numbers narrated in parts 3–4 are the 12 of 16 on port 8011.

**Which server.** 8010 is live and is what the phone talks to (`RESOLVE_DB=data/resolve-demo.sqlite`, 30 tickets). 8011 is demo mode with 60 seeded tickets.

| Clip | Server | Length | Exactly what happens |
|---|---|---|---|
| web-1 Incoming | 8010 | 8 s | Open `/ui`. Slow scroll down the list. Don't open anything. |
| web-2 Ticket | 8010 | 8 s | Open a ticket that is already triaged (status `proposed`). Show the options and the evidence. Don't accept. Don't open an untriaged ticket: that may start a model call. |
| web-3 Chat | 8010 | 8 s | Open Chat with an existing session if one is listed, showing tool calls and allowed/blocked. If none is listed, send one short message as a seeded user (one model call). |
| web-4 Settings | 8010 | 8 s | Open Agent settings. Scroll past tools, limits, tone, handoff rules. Change nothing. |
| web-5 Analyze | 8010 | 8 s | Open Analyze. Scroll from "Machines vs humans" to "Why chats went to people". |
| web-6 Insights | 8011 | 8 s | Open `/insights` by URL (it isn't in the new menu and has the older look). Show "Category × paywall seen". |
| web-7 Impact | 8010 | 8 s | Open `/impact`. Top of the page only, no scrolling: Milos reveals the rest. |
| phone-happy | 8010 | 20 s of final footage (raw wait can be cut) | FitBuddy, Assistant tab, "Chat as" `lumen_443814_00437` (weekly, active, no earlier grant in this DB; confirm it is still paying), "New chat". Type: **"Hi, I pay every week but since this morning the app shows the upgrade screen again. I already tried Restore Purchases and reinstalling. Can you help?"** The reply must show a grant of free days. Replies take 2–4 s. If the agent doesn't grant, use `lumen_443814_04015`. |
| phone-handoff | 8010 + `wt/translate` | 28 s of final footage | "Chat as" `lumen_443814_01800` (monthly, active), "New chat". Type: **"Здравствуйте. Вчера списали деньги за месячную подписку, хотя я не собирался её продлевать. Верните, пожалуйста, деньги."** The Level 2 reply must be in Russian and hand off. Hold on the handoff state. Keep this chat open for the reply later. |
| web-ticket | 8010 + `wt/translate` | 32 s of final footage, in order | (1) Incoming: the new ticket from phone-handoff is at the top, triaged (about 10 s after handoff; cut the wait). 6 s. (2) Open it. Show the message and **every** reply option in English, with "Translated from Russian" and "Sends in Russian" visible. Move slowly across the options. 14 s. (3) Pick an option and send, one click. 4 s. (4) Phone: the Russian reply appears as "Support teammate" (polls every 3 s; cut the wait). 5 s. (5) Back on Incoming: Open/Closed counters changed, then Analyze with the updated numbers. 3 s. |
| web-insights | 8011 | 7 s | `/insights`: "Charged after trial" row with **Onboarding - Weekly first 12, Annual trial 4**, then the Hypothesis panel, then "Drafts created". |
| impact | 8010 | 2:20 raw, one take | `/impact`, 90% zoom if needed. Stop 1 top, hold 40 s. Scroll about 2 s. Stop 2 Lumen table, hold 20 s. Stop 3 three cards, hold 35 s. Stop 4 Adapty section, hold 40 s. Validate next, hold 12 s. Holds are longer than the sheet so either read fits; trim to his audio. |

## Sources

| Number | Where it comes from |
|---|---|
| 21,825 reviews, 1 in 6, 2 of 353 | Deck, slide `data`; Purchase Pain Index |
| 74 apps, 8 categories, 2026 | Deck, slide `data` |
| 202 / 112 / 37 / 2, 136 | Purchase Pain Index, "Support is where it breaks", "And then it costs real money" |
| 12 of 16 (one onboarding flow) | Milos's run sheet; `/impact`; `/insights` on port 8011 |
| 7-day trial, $4.99 two hours later | `kb/E-real-cases.md`, E1 (paraphrase) |
| Part 5 numbers | Milos's run sheet; `resolve/static/ui/Impact.dc.html` |
| "Checked in code", handoff rules, reply times | `README.md`, `docs/agents.md`, `appy/README.md` |
