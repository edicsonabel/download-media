# bash completion for download-media

_download_video() {
  local cur prev opts
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  opts="-a --audio -q --quality -f --format --video-format -o --output -l --list \
        -p --playlist -P --no-playlist -s --subs --sub-langs --cookies \
        -i --interactive -y --yes -v --version -h --help"

  case "$prev" in
    -q|--quality)
      COMPREPLY=( $(compgen -W "360 480 720 1080 1440 2160 best" -- "$cur") )
      return 0 ;;
    -f|--format)
      COMPREPLY=( $(compgen -W "mp3 m4a opus wav" -- "$cur") )
      return 0 ;;
    --video-format)
      COMPREPLY=( $(compgen -W "mp4 mkv webm" -- "$cur") )
      return 0 ;;
    --cookies)
      COMPREPLY=( $(compgen -W "firefox chrome chromium brave edge opera vivaldi safari" -- "$cur") )
      return 0 ;;
    -o|--output)
      COMPREPLY=( $(compgen -d -- "$cur") )
      return 0 ;;
    --sub-langs)
      return 0 ;;
  esac

  if [[ "$cur" == -* ]]; then
    COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
  fi
}

complete -F _download_video download-media
