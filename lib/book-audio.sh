# Shared prompts for to-m4b / to-mp3. Sourced, not executed.
# Expects C_* colors, SEP, banner, picked, need.

BOOK_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/book-audio.py"

# Prints the book narrator. Empty when the files do not share one reader.
ask_narrator() {
  local fallback="$1"
  local out key value mode readers reply
  mode="ask"
  readers=""
  out=$(BOOK_NARRATOR="$fallback" python3 "$BOOK_PY" narrators || true)
  while IFS=$'\t' read -r key value; do
    case "$key" in
      mode) mode=$value ;;
      reader) readers="${readers}${value}"$'\n' ;;
    esac
  done <<< "$out"
  if [[ "$mode" == "each" ]]; then
    echo "${C_MUTED}Narrators differ. Each file keeps its own reader.${C_RESET}" >&2
    echo "${C_MUTED}The book artist is one word, so Books does not say Unknown Author. Enter is Сборник.${C_RESET}" >&2
    echo "${C_MUTED}On the mp3 copies the album artist stays the author.${C_RESET}" >&2
    while IFS= read -r reply; do
      [[ -n "$reply" ]] && echo "${C_MUTED}  ${reply}${C_RESET}" >&2
    done <<< "$readers"
    local default="${fallback:-Сборник}"
    reply=$(ask "Book artist" "$default")
    while [[ "$(python3 "$BOOK_PY" kind "$reply" || true)" != "label" ]]; do
      echo "Type one word, such as Сборник." >&2
      reply=$(ask "Book artist" "$default")
    done
    printf '%s' "$reply"
    return
  fi
  if [[ -z "$fallback" && -z "${readers//[$'\n']/}" ]]; then
    echo "${C_MUTED}No narrator in the folder or the files.${C_RESET}" >&2
    echo "${C_MUTED}Enter leaves it empty. Or type the name: Имя Фамилия.${C_RESET}" >&2
    reply=$(ask "Narrator" "")
    printf '%s' "$reply"
    return
  fi
  reply=$(ask "Narrator" "$fallback")
  while [[ -z "$reply" ]]; do
    echo "Enter the narrator." >&2
    reply=$(ask "Narrator" "$fallback")
  done
  printf '%s' "$reply"
}

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

# Prints the labels on stdout. Questions and the contents line go to stderr.
ask_chapter_labels() {
  local pattern_out sample groups words tail labels parts count part line slots
  labels=""
  sample=""
  groups=""
  words=""
  tail=""
  pattern_out=$(BOOK_TITLE="${BOOK_TITLE:-}" python3 "$BOOK_PY" pattern || true)
  while IFS=$'\t' read -r key value; do
    case "$key" in
      sample) sample=$value ;;
      groups) groups=$value ;;
      words) words=$value ;;
      tail) tail=$value ;;
    esac
  done <<< "$pattern_out"
  if [[ -n "$groups" ]]; then
    echo "${C_MUTED}The file names have numbers like ${sample}. They can be at the start, in the middle, or at the end.${C_RESET}" >&2
    echo "${C_MUTED}One word names only the last number. Example: Глава.${C_RESET}" >&2
    echo "${C_MUTED}A comma names every number: Часть, Глава. Enter keeps the book title.${C_RESET}" >&2
    labels=$(ask "Numbers mean" "")
    while [[ -n "$labels" ]]; do
      count=0
      slots=0
      IFS=',' read -ra parts <<< "$labels"
      slots=${#parts[@]}
      for part in "${parts[@]}"; do
        part="${part#"${part%%[![:space:]]*}"}"
        part="${part%"${part##*[![:space:]]}"}"
        [[ -n "$part" ]] && count=$((count + 1))
      done
      if [[ "$count" -eq 0 ]]; then
        labels=""
        break
      fi
      if [[ "$count" -le "$groups" && "$slots" -le "$groups" ]]; then
        break
      fi
      echo "Type Глава for the last number, or ${groups} names with a comma between them." >&2
      labels=$(ask "Numbers mean" "")
    done
  elif [[ -n "$tail" ]]; then
    echo "${C_MUTED}The file names end with a number like ${tail}.${C_RESET}" >&2
    echo "${C_MUTED}Enter leaves that number out. It is only the file order.${C_RESET}" >&2
    echo "${C_MUTED}Type one word when the number is a part. Example: Глава or Часть.${C_RESET}" >&2
    labels=$(ask "Numbers mean" "")
    while [[ -n "$labels" && ! "$labels" =~ ^[^,[:space:]]+$ ]]; do
      echo "Press Enter to leave the number out, or type one word such as Глава." >&2
      labels=$(ask "Numbers mean" "")
    done
    if [[ -z "$labels" ]]; then
      labels="-"
    fi
  elif [[ -n "$words" ]]; then
    echo "${C_MUTED}The file names already name the part and the chapter.${C_RESET}" >&2
  fi
  if [[ -n "${BOOK_YEAR:-}" ]]; then
    line=$(BOOK_TITLE="${BOOK_TITLE:-}" BOOK_YEAR="${BOOK_YEAR}" BOOK_NARRATOR="${BOOK_NARRATOR:-}" BOOK_CHAPTER_LABELS="$labels" python3 "$BOOK_PY" contents || true)
    if [[ -n "$line" ]]; then
      echo "${C_MUTED}Contents: ${line}${C_RESET}" >&2
    fi
  fi
  printf '%s' "$labels"
}

run_book_audio() {
  local mode="$1"
  local guess_out count first last title author sort narrator reencode
  local genre reply num name year books file_name author_full reader labels

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
    echo "${C_MUTED}Contents: 01. Title · 1863. A part or the file's own title replaces the book title. A file with its own year keeps it: 01. Wolf · 1908. Split files are Part 1.${C_RESET}"
  else
    echo "${C_MUTED}File names are 01. Title · 1863, 01. Title · Chapter 1, or 01. Parables · Wolf · 1908 when the file has its own year.${C_RESET}"
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
    echo "${C_MUTED}Wikipedia: ${author_full}. Enter keeps it. The file name keeps the short author.${C_RESET}"
  else
    echo "Type the author's full name."
    author_full=$author
  fi
  author_full=$(ask "Author full" "$author_full")
  while [[ -z "$author_full" ]]; do
    echo "Enter the author's full name."
    author_full=$(ask "Author full" "$author")
  done

  narrator=$(ask_narrator "$narrator")

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

  labels=$(BOOK_TITLE="$title" BOOK_YEAR="$year" BOOK_NARRATOR="$narrator" ask_chapter_labels)

  books="${author} · ${title} · ${year}"
  if [[ -n "$narrator" ]]; then
    file_name="${author} · ${title} [${narrator}] · ${year}"
    reader=$(python3 "$BOOK_PY" reader "$narrator")
  else
    file_name="$books"
    reader="each file"
  fi

  banner "Summary"
  local kind=""
  if [[ -n "$narrator" ]]; then
    kind=$(python3 "$BOOK_PY" kind "$narrator" || true)
  fi
  if [[ "$kind" == "label" ]]; then
    echo "${C_MUTED}Author field is the full name. The book artist is this word. Each file keeps its reader.${C_RESET}"
  elif [[ -n "$narrator" ]]; then
    echo "${C_MUTED}Author field is the full name. Performer, narrator and artist are Surname Name.${C_RESET}"
  elif [[ "$mode" == "mp3" ]]; then
    echo "${C_MUTED}Author field is the full name. Album artist is the author. Each file's artist is its reader.${C_RESET}"
  else
    echo "${C_MUTED}Author field is the full name. Artist stays empty.${C_RESET}"
  fi
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
  BOOK_CHAPTER_LABELS="$labels" \
  python3 "$BOOK_PY" "$mode"

  picked "Done"
}
