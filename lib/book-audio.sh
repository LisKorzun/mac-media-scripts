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
  local genre reply num name year books file_name author_full reader

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
    echo "${C_MUTED}Copies for the LG TV and Books. Numbered files. Author and narrator are both stored. Source files stay.${C_RESET}"
  fi
  echo "${C_MUTED}Every audio file in this folder is a chapter, in name order.${C_RESET}"
  if [[ "$mode" == "m4b" ]]; then
    echo "${C_MUTED}Contents match the file names: 01. Title · 1863. A description replaces the title.${C_RESET}"
  else
    echo "${C_MUTED}File names are 01. Title · 1863. The number stays in front so the TV sorts 2 before 10.${C_RESET}"
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

  author_full=""
  full_out=$(BOOK_AUTHOR="$author" python3 "$BOOK_PY" author-full || true)
  while IFS=$'\t' read -r key value; do
    case "$key" in
      full) author_full=$value ;;
    esac
  done <<<"$full_out"
  if [[ -n "$author_full" ]]; then
    echo "${C_MUTED}Wikipedia: ${author_full}. Enter keeps it. This name is only the author field.${C_RESET}"
  else
    echo "Type the author's full name."
    author_full=$author
  fi
  author_full=$(ask "Author full" "$author_full")
  while [[ -z "$author_full" ]]; do
    echo "Enter the author's full name."
    author_full=$(ask "Author full" "$author")
  done

  narrator=$(ask "Narrator" "$narrator")
  while [[ -z "$narrator" ]]; do
    echo "Enter the narrator."
    narrator=$(ask "Narrator" "")
  done

  banner "Year"
  echo "${C_MUTED}The year the work was written, not a later edition.${C_RESET}"
  year=""
  year_out=$(BOOK_TITLE="$title" BOOK_AUTHOR="$author" python3 "$BOOK_PY" year || true)
  while IFS=$'\t' read -r key value; do
    case "$key" in
      year) year=$value ;;
    esac
  done <<<"$year_out"
  if [[ -n "$year" ]]; then
    echo "${C_MUTED}Wikipedia: written in ${year}. Enter keeps it.${C_RESET}"
  else
    echo "No year found. Type the year it was written."
  fi
  year=$(ask "Year" "$year")
  while [[ ! "$year" =~ ^[0-9]{4}$ ]]; do
    echo "Enter a 4-digit year."
    year=$(ask "Year" "")
  done

  genre=$(ask "Genre" "Audiobook")
  if [[ -z "$genre" ]]; then
    genre="Audiobook"
  fi

  books="${author} · ${title} · ${year}"
  file_name="${author} · ${title} [${narrator}] · ${year}"

  reader=$(python3 "$BOOK_PY" reader "$narrator")

  banner "Summary"
  echo "${C_MUTED}Author field is the full name. Performer, narrator and artist are Surname Name.${C_RESET}"
  echo "  chapters  $count"
  echo "  author    $author_full"
  echo "  narrator  $reader"
  echo "  year      $year"
  echo "  books     $books"
  echo "  genre     $genre"
  if [[ "$mode" == "m4b" ]]; then
    echo "  audio     AAC stereo 96 kbps"
    echo "  file      ${file_name}.m4b"
  else
    if [[ "${reencode:-0}" -eq 0 ]]; then
      echo "  audio     copy (mp3 sound stays)"
    else
      echo "  audio     ${reencode} file(s) encoded to MP3 128 kbps stereo; mp3 files are copied"
    fi
    echo "  folder    ${file_name}/"
    echo "  album     $books"
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
  BOOK_AUTHOR_FULL="$author_full" \
  BOOK_NARRATOR="$narrator" \
  BOOK_GENRE="$genre" \
  BOOK_YEAR="$year" \
  python3 "$BOOK_PY" "$mode"

  picked "Done"
}
