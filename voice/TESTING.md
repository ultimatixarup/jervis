# Manual checklist — `voice`

The automated suite covers everything that can be driven from a wav file. These need
a person, a microphone and a working brain.

```bash
cd ~/code/jervis
uv run pytest voice        # must be green first
scripts/doctor.sh          # microphone must PASS
```

## 1. The audio stack alone

```bash
scripts/test-voice.sh
```

Say **"Hey Jarvis, what time is it"**. Expect the wake score, the captured duration,
the transcript, and Jervis reading it back. This never touches the brain, so a failure
here is audio, not the agent.

If the wake word never fires: lower `voice.wake_threshold` (try 0.4) and check the
mic is not muted. If it fires constantly: raise it.

## 2. A full spoken turn

```bash
scripts/start.sh
```

Say **"Hey Jarvis"**, wait for the chime, then **"what time is it"**. Expect a spoken
answer within about three seconds of when you stop talking. Locally the pipeline costs
roughly 350ms (transcription plus starting `say`); the rest is the model.

## 3. Confirmation, by voice

Make a throwaway file first:

```bash
touch ~/Desktop/test.txt
```

Say **"Hey Jarvis"**, then **"delete the file test dot txt on my desktop"**. Expect
Jervis to read the action back and ask. Then:

- Say **"no"** — the file must still be there.
- Do it again and say **"yes"** — the file goes to the Trash, and `jervis audit` shows
  `confirmed: true`.
- Do it again and say **nothing** — after 60 seconds it gives up, and nothing happens.

Silence must never count as consent. If it ever does, stop and treat it as a bug.

## 4. Barge-in

Ask something with a long answer, then say **"Hey Jarvis"** while it is still talking.
It should stop mid-sentence and listen.

## Known limits

- **The wake word is "Hey Jarvis", not "Jervis".** That is openwakeword's built-in
  model. `scripts/train-wakeword.sh` explains how to train a "jervis" one; nothing
  else is blocked on it.
- The menu-bar status indicator (PLAN.md §4 Phase 3, marked optional) is not built.
- `say` output can reach the microphone. It has not caused a false wake in practice -
  Jervis does not say "hey jarvis" - but headphones remove the question entirely.
- First run downloads the Whisper model (~500MB) and loads it in about 2s. That happens
  once, at startup, not per turn.
