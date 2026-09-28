# Bundled components

The Windows installer includes a private Python 3.12 runtime and the Python
packages listed in `requirements-runtime.txt`, plus `pystray`. Their package
metadata and license files are preserved under `python/Lib/site-packages`.

It also ships these separate executables:

- FFmpeg, including libass: https://ffmpeg.org/legal.html . The Windows build
  used by the release pipeline comes from the Chocolatey `ffmpeg` package.
  FFmpeg is invoked as a separate program. See the package's build configuration
  and license for the applicable GPL/LGPL terms and corresponding source.
- Deno: https://github.com/denoland/deno/blob/main/LICENSE.md . It is used by
  yt-dlp to process YouTube's JavaScript challenges.
- Codex CLI: https://github.com/openai/codex/blob/main/LICENSE . AIR3view
  launches the bundled native CLI only when the user selects Codex as provider.

VieNeu and faster-whisper model weights are not part of the installer. Their
respective libraries download model files on first use to the user's data
directory. An AI account/API key and network access may still be needed.
