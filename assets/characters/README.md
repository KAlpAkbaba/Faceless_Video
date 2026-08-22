# Character references

The pipeline animates each shot **from a picture**, not from a description.
That is what keeps Benny looking like Benny in episode ninety.

## What to put here

One master still per character, transparent or plain white background,
full body, neutral friendly pose:

```
benny.png   lila.png   milo.png   pip.png   luna.png   bobo.png
```

Filenames are the character names in lowercase, and they must match the names
used in `channel.characters` in `config.yaml`.

Expression and pose sheets go in `sheets/` and are not read by the pipeline —
they are for you, and for regenerating a master still if one is ever lost:

```
sheets/benny-expressions.png
sheets/supporting-cast.png
```

## Why the prompts never describe appearance

`prompts/script_kids.md` forbids the writer from mentioning fur colour,
clothing or species. Every such word competes with the reference image and
pulls the result away from it. The prompt says what the character *does*;
the picture says what they *are*.

If a character's look needs to change, change the picture here — not the
prompt, and not the config text.

## Keeping the look stable

- Never replace a master still casually. Viewers notice, and a channel's value
  is that the characters are the same every time.
- Keep the originals (and the prompt or tool used to make them) somewhere
  outside this repo, so a master can be reproduced exactly.
- If you add a character, add it to `channel.characters` in the same commit.
