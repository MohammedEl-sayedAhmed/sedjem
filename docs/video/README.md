# The Sedjem video

A 60-second product video of the app, 1920 x 1080, 30 frames a second, H.264, in two versions that
differ only in the music:

- `sedjem-tech.mp4`: a darker, electronic track in A minor, with a filtered synth sequence, digital
  bleeps and two glitch stutters (the one the main README links to);
- `sedjem-bright.mp4`: a brighter track in C major, with a soft pad, plucks and a gentle groove.

Both tracks are original, synthesised from nothing but code (`music/tech.py` and `music/bright.py`,
numpy and soundfile only), so they are published with the project under its licence. Both follow the
video scene by scene at 120 beats a minute: the drop on the logo at 6 s, a layer added with each step, a
breakdown under the compare scene so its text can be read, the full groove again for the services at
44 s, the build from 48 s and the end card at 54 s. Small accents land on the screen's moments: each
transcript line in Step 2, each "Kept" and the merge in the compare scene; the tech track also stutters
at 25.5 s and 33.5 s, where the video glitches. The scene and accent times are constants at the top of
each script. The GIF near the top of the main README (`docs/images/sedjem-preview.gif`) is cut from the
video.

The video is a web page. `index.html` lays out every scene with the app's own colours, fonts, radii and
shadows (see `app/static/app.css`), and one paused [GSAP](https://gsap.com) timeline in the same file
moves everything. [HyperFrames](https://github.com/heygen-com/hyperframes) (Apache-2.0) seeks that
timeline frame by frame in Chrome and encodes the frames. Every frame depends only on its time, so a
render gives the same pictures each time.

Everything shown is made up: the meeting, the names, the lines and the file.

## Files

- `index.html`: the scenes, their timing (`data-start` and `data-duration` on each scene) and the
  timeline script.
- `style.css`: the layout and the app's colour tokens, drawn at about 1.7 times the app's size.
- `assets/brands.js`: a copy of `app/static/brands.js`, which names the brands and holds the app's
  logos. Every logo in the video is the brand's own file (`assets/logos/`, copied from
  `app/static/logos/`, with `SOURCES.md` saying where each came from and on what terms), on a white
  tile as in the app. `assets/icon.svg` is the app's icon. The logo in the video is
  `docs/brand/sedjem-logo-colour.svg` placed inline, so its parts can move: the ear pops in and its inner
  curve is drawn, the letters rise, the ear drops in as the dot of the j and the cursor blinks.
- `fonts/`: IBM Plex Sans, IBM Plex Sans Arabic and IBM Plex Mono (the app's fonts), and Patrick Hand
  for the handwritten opening line. All are under the SIL Open Font License, in `OFL-IBM-Plex.txt` and
  `OFL-Patrick-Hand.txt`.
- `package.json`: pins HyperFrames and GSAP. `node_modules/` and `renders/` are not committed.

## Timing

The video is cut to a 120 BPM track in 4/4, so a beat is 0.5 s and a bar 2 s. Scenes start on even
seconds and smaller movements land on half seconds. Two short digital stutters (25.5 s and 33.5 s)
match glitches in the tech track. The video itself stays silent; the music is added to it separately.

It is paced to be read: each headline and its subline stay still for at least 3 seconds after they
land, typing, dragging and clicks go at an easy speed, and no more than one new piece of text appears
in any half second.

| Starts at | Scene |
| --- | --- |
| 0 s | "Meetings don't speak one language.", then an Arabic line with English words types itself right to left |
| 6 s | The logo and the tagline (the music drops here) |
| 10 s | Step 1, Add: planning.m4a is dragged in and let go at 13 s, then transcribing runs, part 1 to 12 of 40 |
| 18 s | Step 2, Read: lines from Mona, Karim and Omar, one a second, each in its own direction; a stutter at 25.5 s |
| 26 s | Step 3, Fix: a word Cohere wrote in Arabic letters is retyped in English, reviewed, saved, and the history shows v0 and v1; a stutter at 33.5 s |
| 34 s | Compare, for 10 s: Cohere Transcribe Arabic (best at Arabic words) beside whisper-medium code-switching (best at keeping English terms in English), the differing words marked, the better row kept in each, merged into one |
| 44 s | Local or hosted: what runs on the computer (Cohere, and models from Hugging Face such as NVIDIA's and Qwen's) and the nine hosted services settle into a grid in their own colours, and the upload needs a tick at 47 s (the music builds from 48 s) |
| 49 s | Any graphics card: Intel, AMD and NVIDIA, with Vulkan on each and CUDA on NVIDIA |
| 51 s | Or none at all: the processor, with Cohere's measured 0.36 times real time on a laptop processor, held to 53.6 s |
| 54 s | The end card: logo, tagline, licence and the repository link, held to 60 s |

The compare rows follow the measured results in `docs/03-results.md`. Cohere makes the fewest errors in
Arabic words but sometimes writes an English term in Arabic letters (فاليوزر for user, لينك for link);
whisper-medium keeps English terms in English but sometimes turns Egyptian into formal Arabic (أريد for
عايزة, اليوم for النهارده). The best of both keeps whisper-medium's row for those two terms and Cohere's
for the Arabic.

## Preview and render

Node.js 22 or newer, FFmpeg and Google Chrome are needed. From this folder:

```sh
npm install                 # HyperFrames and GSAP, into node_modules/
npx hyperframes preview     # opens the studio in the browser, with a scrubbable timeline
npx hyperframes render --fps 30 --quality delivery --workers 1 --no-browser-gpu --output renders/sedjem-raw.mp4
```

Render with one worker and software drawing, as above. With several workers each draws its own part of
the video, and text moved or scaled by an animation came out a pixel different at the joins and on
some frames, which showed as a shimmer. With the graphics card, a faded element sometimes left a ghost
on the next frame. One software worker takes about three and a half minutes and gives the same
picture every time.

The MP4 the music is added to is that render encoded again, smaller and ready to stream from the start:

```sh
ffmpeg -i renders/sedjem-raw.mp4 -an -c:v libx264 -preset slow -crf 20 -tune animation \
  -pix_fmt yuv420p -movflags +faststart renders/sedjem.mp4
```

The music is made and added like this (with the project's Python, which has numpy and soundfile):

```sh
python music/tech.py renders/tech.wav        # or music/bright.py renders/bright.wav
# bring it to -14 LUFS: measure with  ffmpeg -i renders/tech.wav -af ebur128 -f null -
ffmpeg -i renders/tech.wav -af volume=<-14 minus the measured loudness>dB -c:a aac -b:a 192k music/tech.m4a
ffmpeg -i renders/sedjem.mp4 -i music/tech.m4a -map 0:v -map 1:a -c copy -shortest -movflags +faststart sedjem-tech.mp4
```

The GIF is the compare scene, from 34.3 s to 43.55 s, at 900 pixels wide and 12 frames a second. Its
last frame is held for a moment and then fades back into its first, so the loop has no jump
(10.7 seconds in all):

```sh
LOOP="[0:v]fps=12,scale=900:-1:flags=lanczos,split=2[a][b];[b]trim=end_frame=1,loop=loop=11:size=1:start=0,setpts=N/12/TB[first];[a]tpad=stop_mode=clone:stop_duration=1.0[a2];[a2][first]xfade=transition=fade:duration=0.6:offset=9.65"
ffmpeg -ss 34.3 -t 9.25 -i renders/sedjem.mp4 -filter_complex "$LOOP,palettegen=stats_mode=diff" renders/palette.png
ffmpeg -ss 34.3 -t 9.25 -i renders/sedjem.mp4 -i renders/palette.png \
  -filter_complex "$LOOP[v];[v][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  -loop 0 ../images/sedjem-preview.gif
```

`thumbnail.png` is the cover to choose when the video is posted (LinkedIn, YouTube and the like): the end
card, with the logo, the tagline and the repository link, taken from the finished video:

```sh
ffmpeg -ss 58.5 -i sedjem-tech.mp4 -frames:v 1 -update 1 thumbnail.png
```

To change a scene, edit its markup and its block in the timeline script (each block is headed with
the second it starts at), then look at single frames before a full render:

```sh
npx hyperframes snapshot --at 20.5,38.5 --no-end
```
