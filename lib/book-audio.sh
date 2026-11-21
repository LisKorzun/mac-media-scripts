# Shared prompts for to-m4b / to-mp3. Sourced, not executed.
# Expects C_* colors, SEP, banner, picked, need.

BOOK_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/book-audio.py"

ask() {
  local label="$1"
  local default="$2"
  local reply
  printf "${C_PROMPT}%s [%s]:${C_RESET} " "$label" "$default" >&2
  read -r reply || reply=""
  if [[ -z "$reply" ]]; then
    reply="$default"
  fi
  printf '%s' "$reply"
}

run_book_audio() {
  local mode="$1"
  local guess_out count first last title author sort narrator reencode
  local author_sort genre reply num name

  need python3
  need ffmpeg
  need ffprobe
  if [[ "$mode" == "m4b" ]]; then
    need exiftool
  fi

  guess_out=$(python3 "$BOOK_PY" guess) || exit 1
  while IFS=$'\t' read -r key value; do
    case "$key" in
      count) count=$value ;;
      first) first=$value ;;
      last) last=$value ;;
      title) title=$value ;;
      author) author=$value ;;
      sort) sort=$value ;;
      narrator) narrator=$value ;;
      reencode) reencode=$value ;;
    esac
  done <<<"$guess_out"

  if [[ "$mode" == "m4b" ]]; then
    banner "Audiobook → M4B"
    echo "${C_MUTED}One file for Apple Books. AAC stereo 96 kbps, chapters inside.${C_RESET}"
  else
    banner "Audiobook → MP3"
    echo "${C_MUTED}Copies for the LG TV and Books. Track, author, narrator. Source files stay.${C_RESET}"
  fi
  echo "${C_MUTED}Every audio file in this folder is a chapter, in name order.${C_RESET}"
  if [[ "$mode" == "m4b" ]]; then
    echo "${C_MUTED}A file with a description keeps that name. The rest are numbered 1, 2, 3.${C_RESET}"
  fi
  echo "${C_MUTED}${count} file(s): ${first} … ${last}${C_RESET}"

  banner "Chapters"
  while IFS=$'\t' read -r num name; do
    printf "${C_PROMPT}%s${C_RESET} %s\n" "$num" "$name"
  done < <(python3 "$BOOK_PY" files)

  banner "Book"
  echo "${C_MUTED}Enter keeps the value in brackets.${C_RESET}"

  title=$(ask "Book title" "$title")
  while [[ -z "$title" ]]; do
    echo "Enter a book title."
    title=$(ask "Book title" "")
  done

  author=$(ask "Author" "$author")
  while [[ -z "$author" ]]; do
    echo "Enter the author."
    author=$(ask "Author" "")
  done

  author_sort=$(python3 "$BOOK_PY" sort-key "$author")
  author_sort=$(ask "Author sort" "$author_sort")
  if [[ -z "$author_sort" ]]; then
    author_sort=$author
  fi

  narrator=$(ask "Narrator" "$narrator")
  genre=$(ask "Genre" "Audiobook")
  if [[ -z "$genre" ]]; then
    genre="Audiobook"
  fi

  banner "Summary"
  echo "  chapters  $count"
  echo "  title     $title"
  echo "  author    $author"
  echo "  sort      $author_sort"
  echo "  narrator  ${narrator:-—}"
  echo "  genre     $genre"
  if [[ "$mode" == "m4b" ]]; then
    echo "  audio     AAC stereo 96 kbps"
    echo "  output    ${title}.m4b"
  else
    if [[ "${reencode:-0}" -eq 0 ]]; then
      echo "  audio     copy (mp3 sound stays)"
    else
      echo "  audio     ${reencode} file(s) encoded to MP3 128 kbps stereo; mp3 files are copied"
    fi
    echo "  output    MP3/  (track, author, narrator)"
  fi
  echo "${C_HEAD}${SEP}${C_RESET}"
  printf "${C_PROMPT}Start? [Y/n]:${C_RESET} "
  read -r reply
  reply=${reply:-Y}
  case "$reply" in
    y|Y|"") ;;
    *) echo "Cancelled"; exit 0 ;;
  esac

  if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -is -w $$ >/dev/null 2>&1 &
    picked "Keeping Mac awake until this script finishes (caffeinate -is)"
  fi

  BOOK_TITLE="$title" \
  BOOK_AUTHOR="$author" \
  BOOK_SORT="$author_sort" \
  BOOK_NARRATOR="$narrator" \
  BOOK_GENRE="$genre" \
  python3 "$BOOK_PY" "$mode"

  picked "Done"
}
