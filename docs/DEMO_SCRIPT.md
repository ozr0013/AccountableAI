# AccountableAI demo script

Seats: *the accounts payable reviewer who confirms every vendor got paid, and the collections
person who chases the middleman when they didn't.*

## Before each judge

```bash
./demo.sh check     # every page answers, the simulation test passes
./demo.sh reset     # Apr 22, 2026, $3,140 unexplained
```

Start with `./demo.sh start` if nothing is running. Rules agents: free, and the same 9/9 as the evals.

## Live demo (about 3 minutes)

**0:00 · The moment (open with it).** Judge Console → **Tell it a fact**: "the electrician's
invoice arrived Apr 15". "Oakline is about to send Keyway $18,400. The system recommends holding
$3,140: three vendors, no proof they were paid. One fact changed. Watch: the router sends it to a
recheck, the chaser rechecks only what it touches, the verifier tries to prove the alert wrong, and
it's withdrawn: $3,140 → $2,240. The other two stay: still no bank record or vendor confirmation."

**0:30 · It defends the call.** Judge Console → **Write an email** as Keyway: "Sparkline was paid
for KW-0402 last week." "Keyway says it paid. The reader reads the email, the chaser says a reply
isn't proof and holds its position, and the policy gate refuses a reminder that comes too soon."

**0:50 · It runs on its own.** Judge Console → **Let time pass** → **+5 days**. "Nobody pressed
anything but the clock: an invoice falls due, its alert comes back, the gate now allows the
reminder, and the hold is re-proposed for the controller." (**Play 15 days** runs the whole script:
$3,140 → $900.)

**1:30 · Real messy paperwork.** Judge Console → **Recorded run: 20 outside PDFs**. "Twenty PDFs we didn't generate: an
agreement, a bank export, a statement, invoices and emails, including a spoofed vendor email and
one with hidden instructions. Sonnet read all 20; every value is checked against the page. The
spoof was quarantined, the hidden instructions did nothing, and fifteen days later it recommends
holding $1,205.50 for the vendors still unproven."

**2:10 · Limits.** Review queue: approve the hold as the controller. "The case agent proposes; it
can't approve its own proposal, and no tool moves money." Guardrails: the refusals on record.

**2:40 · Proof.** Proof & evals: 9/9 planted scenarios and the moment, against 8/9 for one strong
model with every document; the verifier stops 8/8 planted bad alerts.

## Say what's simulated

- **Real:** the workflow, agents, parser, verifier, policy gate, event log, tests; the 20 outside
  PDFs and their run (`world/pack/`).
- **Simulated:** the demo company (Oakline, Keyway, 20 vendors), the demo clock (time moves when the
  simulation or a judge moves it), and reminders, which are recorded, not emailed.
- **How we know:** the answer key (`evals/answer_key.json`), 9/9 with baselines, 8/8 adversarial,
  and `dashboard/feed/tests/test_simulation.py`, which fails if the simulation shows anything the
  backend doesn't do.

## If a judge brings a scenario

Judge Console takes their own input four ways: one of our documents or their own file (PDF, .eml,
bank CSV), an email they write as Keyway or any vendor, a fact in plain words, or time. Tested facts:
"the electrician's invoice arrived Apr 15", "Keyway says they paid the electrician", "Rio Grande
Plumbing confirmed they were paid". It refuses what it can't record as a fact or tie to one invoice,
and suggests phrasings it can. Their own file goes through the same reader; outside paperwork that
names invoices these books don't have is read and refused with the reason. For a whole outside pack, use
`world/pack/run.py` (Sonnet reads and checks; the 20-file run over 15 simulated days cost $1.22).

## Video (75 seconds)

0–20 s the moment ($3,140 → $2,240) · 20–35 s "we paid" held · 35–55 s +5 days, on its own ·
55–70 s the 20 outside PDFs · 70–75 s the seats line.
