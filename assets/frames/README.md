# First-frame stills

Each shot is animated **from one of these pictures**, which becomes its first
frame. So these must look like frames of the show, not like reference sheets:
characters standing in a real place, proper lighting, proper framing.

A white-background character sheet used here produces a shot of the characters
floating on white. The sheets live in `assets/characters/` and are not sent to
the API.

## Naming

The filename says who is in the picture. Lowercase character names, separated
by hyphens, anything else after them:

```
benny-park.png
lila-kitchen.png
benny-lila-playroom.png
milo-garden.png
bobo-toybox.png
pip-lila-workshop.png
```

The pipeline matches a shot to a frame by the characters the writer put in that
shot, preferring a frame that names all of them, then one that names the lead,
then anything. It rotates through the matches so consecutive shots do not all
start from the same picture.

## Shot one is not just another frame

The first shot is what the viewer sees first and where the thumbnail comes
from, so its frame carries two rules the others do not:

- **It shows the problem.** If the episode opens on "where did all the colours
  go?", the frame is a world with no colour in it.
- **It does not show the solution.** If the story is about finding three colour
  boxes at the top of a hill, the boxes are not in the opening frame. A first
  frame that gives away the discovery throws away the reason to keep watching.

Read shot one of the storyboard and build the frame to match it, not the other
way round. It is much cheaper to make a picture than to rewrite an episode.

More frames means more variety. A dozen covering the usual places is a
reasonable start; add a new one whenever an episode needs a place you do not
have yet.

## Keeping the look

Make these from the character sheets so the cast stays identical, and keep
whatever you used to make them. If a frame is ever lost it must be
reproducible — the whole point is that Benny looks like Benny in episode
ninety.
