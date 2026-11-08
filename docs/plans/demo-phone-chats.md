# Phone retake

Tested on main 5dd0881, read-only copy, 8 Nov. Only 2 runs per chat (time cut), so 2/2 is thin evidence.

## Reconnect the phone first

The Mac moved off 192.168.1.114. It was 10.156.121.93 when I checked; confirm it.

1. Mac: `ipconfig getifaddr en0`
2. Phone browser: `http://<ip>:8010/api/users` should show JSON.
   Hangs: Wi-Fi isolation or the firewall. Use a phone hotspot (the IP changes, redo step 1).
3. `appy/.env.local`: `EXPO_PUBLIC_AGENT_API_URL=http://<ip>:8010`
4. Restart Expo, because the URL is baked in at start. It's been running since 04:32 on the old network:
   Ctrl-C, then in `appy/`: `npx expo start --lan -c --port 8081`
5. Scan the new QR.

Expect: a yellow banner with `Server: http://192.168.1.114...` means the old bundle. Force-close the app and scan again.

Before each take: tap **New chat**. App reload also starts new sessions. Old tickets stay in Incoming.

## Chat 1: happy path, free days

**Chat as:** …04855 (monthly, App Store, until Nov 30)

**Type:**
> I pay monthly but premium workouts are locked.

**Expect:** "added 7 free days… runs to Dec 7", plus Restore steps. No handoff, no ticket.

**Reliability:** grant 1/2. No handoff 2/2. The other run gave Restore steps only.

**If you get Restore steps only** (no days), type this. UNTESTED:
> Restore didn't help. Still locked.

**Bad take:** no "7 free days" in the reply, or "passed to a teammate".

**Fallback:** …01656 (monthly, Google Play), same line. UNTESTED. Its steps will say Google account, not Apple ID.

**One take per user.** On the live server the grant is real, and the limit is 1 per 30 days. After a grant, a second take on …04855 is blocked. Move to …01656.

## Chat 2: ticket catches the meaning

**Chat as:** …00428 (annual, App Store, until Oct 20 2027, product annual_trial)

**Type, one at a time. Wait for each reply:**
1. > Can I talk to a human?
2. > Can I talk to the assistant while I wait?
3. > My free trial turned into a yearly charge. I did not expect it.

Don't write "instead": it withdraws the ticket.

**Expect in chat:**
1. "Passed to a teammate", plan active until Oct 20 2027.
2. "Go ahead / happy to help while you wait."
3. Empathy, refunds are decided by Apple, offers the steps.

**Expect on the web ticket** (wait about 25 s after line 1, about 15 s after lines 2 and 3):
- Lines 1 and 2: category `other`, "asked to speak to a human".
- Line 3: category `charged_after_trial`, "wants the yearly charge that followed the trial refunded".

**Reliability:** ticket moved to `charged_after_trial` 2/2. Chat reply without arguing 1/2.

**Bad take:**
- Reply 3 says "no free trial attached" (happened 1/2). The agent is arguing, so redo.
- The ticket text can mention an old refund request or a weekly plan on another account (2/2 at some stage). This user has earlier chats. Don't linger on the option text, and don't send any option on camera.

**Fallback:** …02380 (annual_trial, App Store, until Oct 15 2027, 1 earlier chat, no tickets), same lines. UNTESTED. Its history is cleaner, so try it first if …00428 looks messy.

## Chat 3: Russian to support

**Chat as:** …00014 (annual, App Store, bought Nov 4, until Nov 4 2027)

**Type:**
> Верните деньги за годовую подписку, пожалуйста

**Expect in chat:** Russian reply, Apple steps (reportaproblem.apple.com), "передал коллеге".

**Expect on the web ticket** (wait about 25 s):
- Category `refund_request`, summary in English.
- Messages translated to English.
- Top option 💸 `refund_to_human`, English text.

**Manager:** accept the top option 💸. After a few seconds this reaches the chat, in Russian:

> Возврат за годовую подписку от 4 ноября — это решение Apple, у нас нет доступа к вашим деньгам. Откройте reportaproblem.apple.com… Ваш премиум остаётся до 4 ноября 2027 года.

**Reliability:** Russian reply + handoff + ticket 2/2. Accept through the real endpoint on a copy 2/2, Russian text reached the chat both times.

**Bad take:**
- English reply in chat, or no handoff.
- Top option isn't 💸.

**Careful:** the third option was 👍 `grant_access` 2/2. Accepting it writes a real grant. Don't pick it.

**Fallback:** …00931 (annual, Google Play, until Oct 19 2027), same line. UNTESTED. Steps will be Google Play.

## Not checked

- Nothing was filmed or run on the phone itself. All runs went through `POST /chat` and the API.
- Fallback users and the "Restore didn't help" line.
- wt/polish, not tested. If 8010 restarts on it, run each chat once before filming:
  - Committed 6154ad4: the ticket screen's evidence labels and title change, and Level 1's prompt changes (bot → assistant). Chats 2 and 3 tickets may read differently.
  - Uncommitted: Russian fixed replies in `frontline.py`. Only fallback messages change, not the normal chat 3 reply.
