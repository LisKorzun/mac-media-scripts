# Shared queue UI + ffmpeg encode for to-apple / to-lg-tv.
# Caller sets: OUT_W CRF PRESET AUDIO_BR OUT_SUFFIX OUT_EXT TARGET_LABEL
#              REJECT_IMAGE_SUBS (1 = block PGS at pick time)
#              ENCODE_SUMMARY_LINE ENCODE_SUBS_LINE

banner() {
  echo
  echo "${C_HEAD}${SEP}${C_RESET}"
  echo "${C_HEAD}$1${C_RESET}"
  echo "${C_HEAD}${SEP}${C_RESET}"
}

picked() {
  echo "${C_PICK}✓ $1${C_RESET}"
}

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "need $1"; exit 1; }
}

probe_streams() {
  local src=$1
  s_idx=()
  s_type=()
  s_codec=()
  s_xfer=()
  s_extra=()
  s_meta=()
  while IFS='|' read -r idx codec_type codec_name w h ch lang title xfer; do
    [[ -z "${idx:-}" ]] && continue
    s_idx+=("$idx")
    s_type+=("$codec_type")
    s_codec+=("$codec_name")
    s_xfer+=("$xfer")
    local extra=""
    case "$codec_type" in
      video) extra="${w}x${h}" ;;
      audio) [[ -n "$ch" ]] && extra="${ch}ch" ;;
    esac
    s_extra+=("$extra")
    local meta=""
    [[ -n "$lang" ]] && meta="$lang"
    if [[ -n "$title" ]]; then
      if [[ -n "$meta" ]]; then
        meta="$meta · $title"
      else
        meta="$title"
      fi
    fi
    s_meta+=("$meta")
  done < <(
    ffprobe -v error -show_entries stream=index,codec_type,codec_name,width,height,channels,color_transfer:stream_tags=language,title -of json "$src" \
    | python3 -c '
import sys, json
d = json.load(sys.stdin)
for s in d.get("streams") or []:
    tags = s.get("tags") or {}
    title = (tags.get("title") or "").replace("|", "/")
    print("|".join([
        str(s.get("index", "")),
        s.get("codec_type") or "",
        s.get("codec_name") or "",
        "" if s.get("width") in (None, "N/A") else str(s.get("width")),
        "" if s.get("height") in (None, "N/A") else str(s.get("height")),
        "" if s.get("channels") in (None, "N/A") else str(s.get("channels")),
        tags.get("language") or "",
        title,
        s.get("color_transfer") or "",
    ]))
'
  )
}

show_streams() {
  local n=${#s_idx[@]}
  local j num color line
  banner "Available streams:"
  for j in "${!s_idx[@]}"; do
    num=$((j + 1))
    color="$C_OTHER"
    case "${s_type[$j]}" in
      video) color="$C_VIDEO" ;;
      audio) color="$C_AUDIO" ;;
      subtitle) color="$C_SUB" ;;
    esac
    line=$(printf "%2d. 0:%s:  %s  %s" "$num" "${s_idx[$j]}" "${s_type[$j]}" "${s_codec[$j]}")
    [[ -n "${s_extra[$j]}" ]] && line+="  ${s_extra[$j]}"
    [[ -n "${s_meta[$j]}" ]] && line+="  ${s_meta[$j]}"
    printf "${color}%s${C_RESET}\n" "$line"
  done
  echo "${C_HEAD}${SEP}${C_RESET}"
  echo "${C_MUTED}Tip: type menu numbers separated by spaces, then Enter.${C_RESET}"
  echo "${C_MUTED}Example: 1 2 7  → video #1, audio #2, subtitle #7${C_RESET}"
  echo "${C_MUTED}Need exactly 1 video and at least 1 audio.${C_RESET}"
  echo "${C_MUTED}${STREAM_HINT}${C_RESET}"
}

streamTypeByMenu() {
  local menu=$1
  local j=$((menu - 1))
  if [[ "$menu" -lt 1 || "$menu" -gt ${#s_idx[@]} ]]; then
    return 1
  fi
  echo "${s_type[$j]}"
}

streamIdxByMenu() {
  local menu=$1
  local j=$((menu - 1))
  echo "${s_idx[$j]}"
}

streamCodecByIdx() {
  local id=$1
  local j
  for j in "${!s_idx[@]}"; do
    if [[ "${s_idx[$j]}" == "$id" ]]; then
      echo "${s_codec[$j]}"
      return 0
    fi
  done
  echo ""
}

isImageSub() {
  case "$1" in
    hdmv_pgs_subtitle|dvd_subtitle|dvb_subtitle|xsub) return 0 ;;
    *) return 1 ;;
  esac
}

pick_streams() {
  local n=${#s_idx[@]}
  local sel m t id sc ok vcount acount
  sel_video=()
  sel_audio=()
  sel_sub=()
  sel_menus_ok=()
  while true; do
    printf "${C_PROMPT}Stream numbers (space-separated):${C_RESET} "
    read -r sel
    sel_menus=($sel)
    vcount=0
    acount=0
    ok=1
    sel_video=()
    sel_audio=()
    sel_sub=()
    sel_menus_ok=()
    for m in "${sel_menus[@]}"; do
      if [[ ! "$m" =~ ^[1-9][0-9]*$ ]]; then
        echo "Bad number: $m"
        ok=0
        break
      fi
      t=$(streamTypeByMenu "$m" || true)
      if [[ -z "$t" ]]; then
        echo "Bad number: $m (valid: 1-$n)"
        ok=0
        break
      fi
      id=$(streamIdxByMenu "$m")
      case "$t" in
        video) sel_video+=("$id"); vcount=$((vcount + 1)) ;;
        audio) sel_audio+=("$id"); acount=$((acount + 1)) ;;
        subtitle)
          if [[ "$REJECT_IMAGE_SUBS" -eq 1 ]]; then
            sc=$(streamCodecByIdx "$id")
            if isImageSub "$sc"; then
              echo "Image subtitles ($sc) cannot go in Apple MP4. Pick a text track or skip subs."
              ok=0
              break
            fi
          fi
          sel_sub+=("$id")
          ;;
        *) echo "Unsupported type $t"; ok=0; break ;;
      esac
      sel_menus_ok+=("$m")
    done
    [[ "$ok" -eq 0 ]] && continue
    if [[ "$vcount" -ne 1 ]]; then
      echo "Need exactly 1 video stream (got $vcount)"
      continue
    fi
    if [[ "$acount" -lt 1 ]]; then
      echo "Need at least 1 audio stream"
      continue
    fi
    break
  done
  echo "${C_MUTED}Chosen numbers: ${sel_menus_ok[*]}${C_RESET}"
  picked "Selected streams: ${sel_menus_ok[*]}"
}

video_hdr() {
  local vid=$1
  local j
  for j in "${!s_idx[@]}"; do
    if [[ "${s_idx[$j]}" == "$vid" ]]; then
      case "${s_xfer[$j]}" in
        smpte2084|arib-std-b67) echo 1; return 0 ;;
      esac
      break
    fi
  done
  echo 0
}

pick_mode() {
  banner "Select conversion mode:"
  echo "1. Whole file (default)"
  echo "2. First N minutes only"
  echo "${C_MUTED}Applies to every file in the queue.${C_RESET}"
  echo "${C_MUTED}How to use mode 2:${C_RESET}"
  echo "${C_MUTED}  1) type 2 and press Enter${C_RESET}"
  echo "${C_MUTED}  2) type minutes as a whole number (example: 5) and press Enter${C_RESET}"
  echo "${C_MUTED}  Do not type decimals. Example for one minute: 1${C_RESET}"
  echo "${C_HEAD}${SEP}${C_RESET}"
  printf "${C_PROMPT}Your choice (1-2) [1]:${C_RESET} "
  read -r mchoice
  mchoice=${mchoice:-1}
  minutes=0
  case "$mchoice" in
    1) picked "Mode: whole file" ;;
    2)
      while true; do
        printf "${C_PROMPT}Minutes (whole number, e.g. 5):${C_RESET} "
        read -r minutes
        if [[ "$minutes" =~ ^[1-9][0-9]*$ ]]; then
          break
        fi
        echo "Enter a whole number >= 1 (example: 5)"
      done
      picked "Mode: first ${minutes} minute(s)"
      ;;
    *) echo "Bad mode choice"; exit 1 ;;
  esac
}

join_words() {
  local IFS=' '
  echo "$*"
}

encode_one() {
  local src=$1
  local out=$2
  local hdr=$3
  local -a vids auds subs
  local -a map_args cmd vf sc si id
  # shellcheck disable=SC2206
  vids=($4)
  # shellcheck disable=SC2206
  auds=($5)
  subs=()
  [[ -n "${6:-}" ]] && subs=($6)

  map_args=()
  for id in "${vids[@]}" "${auds[@]}"; do
    map_args+=(-map "0:$id")
  done
  for id in "${subs[@]}"; do
    map_args+=(-map "0:$id")
  done

  vf="scale=${OUT_W}:-2"
  if [[ "$hdr" -eq 1 ]]; then
    vf="zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=${OUT_W}:-2"
  fi

  cmd=(ffmpeg -hide_banner -y -i "$src")
  if [[ "$minutes" -gt 0 ]]; then
    cmd+=(-t $((minutes * 60)))
  fi
  cmd+=("${map_args[@]}")
  cmd+=(-vf "$vf")
  cmd+=(-c:v libx265 -crf "$CRF" -preset "$PRESET" -pix_fmt yuv420p)
  if [[ "$OUT_EXT" == "mp4" ]]; then
    cmd+=(-tag:v hvc1)
  fi
  cmd+=(-c:a aac -ac 2 -b:a "$AUDIO_BR")

  if [[ ${#subs[@]} -gt 0 ]]; then
    if [[ "$OUT_EXT" == "mp4" ]]; then
      cmd+=(-c:s mov_text -disposition:s:0 default)
    else
      probe_streams "$src"
      si=0
      for id in "${subs[@]}"; do
        sc=$(streamCodecByIdx "$id")
        if isImageSub "$sc"; then
          cmd+=(-c:s:$si copy)
        else
          cmd+=(-c:s:$si srt)
        fi
        si=$((si + 1))
      done
      cmd+=(-disposition:s:0 default)
    fi
  fi
  cmd+=(-disposition:a:0 default)
  if [[ "$OUT_EXT" == "mp4" ]]; then
    cmd+=(-movflags +faststart)
  fi
  cmd+=("$out")

  "${cmd[@]}"
  xattr -c "$out" 2>/dev/null || true
}

run_fixed_profile_queue() {
  local files=()
  local f i pick pick_menus m src prev_menus conf
  local -a queue_src queue_out queue_hdr queue_video queue_audio queue_sub
  local qi total failed=() done_n=0
  local base out_suffix out subs_show hdr

  need ffmpeg
  need ffprobe
  need python3

  while IFS= read -r -d '' f; do
    files+=("$f")
  done < <(find . -maxdepth 1 -type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' -o -iname '*.avi' -o -iname '*.webm' -o -iname '*.ts' -o -iname '*.m2ts' \) -print0 | sort -z)

  if [[ ${#files[@]} -eq 0 ]]; then
    echo "No video files in $(pwd)"
    exit 1
  fi

  banner "Found video files:"
  i=1
  for f in "${files[@]}"; do
    printf "${C_PROMPT}%d.${C_RESET} %s\n" "$i" "${f#./}"
    i=$((i + 1))
  done
  echo "${C_HEAD}${SEP}${C_RESET}"
  echo "${C_MUTED}Pick one or more numbers separated by spaces (example: 1 3 5).${C_RESET}"

  while true; do
    printf "${C_PROMPT}Select video number(s) to convert (1-%d):${C_RESET} " "${#files[@]}"
    read -r pick
    pick_menus=($pick)
    if [[ ${#pick_menus[@]} -eq 0 ]]; then
      echo "Enter at least one number from 1 to ${#files[@]}"
      continue
    fi
    local ok=1 seen=""
    for m in "${pick_menus[@]}"; do
      if [[ ! "$m" =~ ^[1-9][0-9]*$ ]] || [[ "$m" -lt 1 || "$m" -gt ${#files[@]} ]]; then
        echo "Bad number: $m (valid: 1-${#files[@]})"
        ok=0
        break
      fi
      if [[ " $seen " == *" $m "* ]]; then
        echo "Duplicate number: $m"
        ok=0
        break
      fi
      seen="$seen $m"
    done
    [[ "$ok" -eq 1 ]] && break
  done
  picked "Queue: ${pick_menus[*]}"

  pick_mode

  prev_menus=""
  for m in "${pick_menus[@]}"; do
    src="${files[$((m - 1))]}"
    banner "Streams for: ${src#./}"
    probe_streams "$src"
    show_streams

    if [[ -n "$prev_menus" ]]; then
      printf "${C_PROMPT}Same stream menu numbers as previous file? [Y/n]:${C_RESET} "
      read -r same
      same=${same:-Y}
      case "$same" in
        y|Y|"")
          sel_menus_ok=($prev_menus)
          sel_video=()
          sel_audio=()
          sel_sub=()
          local t id sc vcount=0 acount=0 ok=1
          for mm in "${sel_menus_ok[@]}"; do
            t=$(streamTypeByMenu "$mm" || true)
            if [[ -z "$t" ]]; then
              echo "Menu $mm not valid for this file — pick streams manually."
              ok=0
              break
            fi
            id=$(streamIdxByMenu "$mm")
            case "$t" in
              video) sel_video+=("$id"); vcount=$((vcount + 1)) ;;
              audio) sel_audio+=("$id"); acount=$((acount + 1)) ;;
              subtitle)
                if [[ "$REJECT_IMAGE_SUBS" -eq 1 ]]; then
                  sc=$(streamCodecByIdx "$id")
                  if isImageSub "$sc"; then
                    echo "Image subtitles ($sc) — pick streams manually."
                    ok=0
                    break
                  fi
                fi
                sel_sub+=("$id")
                ;;
              *) ok=0; break ;;
            esac
          done
          if [[ "$ok" -eq 1 && "$vcount" -eq 1 && "$acount" -ge 1 ]]; then
            echo "${C_MUTED}Reused menu numbers: ${sel_menus_ok[*]}${C_RESET}"
            picked "Selected streams: ${sel_menus_ok[*]}"
          else
            pick_streams
          fi
          ;;
        *) pick_streams ;;
      esac
    else
      pick_streams
    fi

    prev_menus="${sel_menus_ok[*]}"
    hdr=$(video_hdr "${sel_video[0]}")
    if [[ "$hdr" -eq 1 ]]; then
      picked "HDR detected → tone-map to SDR"
    fi

    base=$(basename "$src")
    base="${base%.*}"
    out_suffix="$OUT_SUFFIX"
    if [[ "$minutes" -gt 0 ]]; then
      out_suffix="${out_suffix}_first${minutes}m"
    fi
    out="./${base}${out_suffix}.${OUT_EXT}"

    queue_src+=("$src")
    queue_out+=("$out")
    queue_hdr+=("$hdr")
    queue_video+=("$(join_words "${sel_video[@]}")")
    queue_audio+=("$(join_words "${sel_audio[@]}")")
    if [[ ${#sel_sub[@]} -gt 0 ]]; then
      queue_sub+=("$(join_words "${sel_sub[@]}")")
    else
      queue_sub+=("")
    fi
  done

  banner "Queue summary:"
  total=${#queue_src[@]}
  for qi in "${!queue_src[@]}"; do
    echo "  [$((qi + 1))/$total] $(basename "${queue_src[$qi]}")"
    echo "           → ${queue_out[$qi]#./}"
    echo "           video ${queue_video[$qi]}  audio ${queue_audio[$qi]}"
    if [[ -n "${queue_sub[$qi]}" ]]; then
      echo "           subs  ${queue_sub[$qi]}"
    else
      echo "           subs  (none)"
    fi
    if [[ "${queue_hdr[$qi]}" -eq 1 ]]; then
      echo "           hdr   tone-map → SDR"
    fi
  done
  echo "  target    $TARGET_LABEL"
  echo "  size      ${OUT_W}px wide (1080p), CRF ${CRF}, x265 ${PRESET}"
  if [[ "$minutes" -gt 0 ]]; then
    echo "  duration  first ${minutes} min (each file)"
  else
    echo "  duration  whole file (each)"
  fi
  echo "  encode    $ENCODE_SUMMARY_LINE"
  if [[ -n "${ENCODE_SUBS_LINE:-}" ]]; then
    echo "  subs-enc  $ENCODE_SUBS_LINE"
  fi
  echo "${C_HEAD}${SEP}${C_RESET}"
  printf "${C_PROMPT}Start queue (${total} file(s))? [Y/n]:${C_RESET} "
  read -r conf
  conf=${conf:-Y}
  case "$conf" in
    y|Y|"") ;;
    *) echo "Cancelled"; exit 0 ;;
  esac

  for qi in "${!queue_src[@]}"; do
    banner "Converting [$((qi + 1))/$total]: $(basename "${queue_src[$qi]}")"
    probe_streams "${queue_src[$qi]}"
    set +e
    encode_one "${queue_src[$qi]}" "${queue_out[$qi]}" "${queue_hdr[$qi]}" \
      "${queue_video[$qi]}" "${queue_audio[$qi]}" "${queue_sub[$qi]}"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      picked "Done: ${queue_out[$qi]#./}"
      done_n=$((done_n + 1))
    else
      echo "Failed: ${queue_src[$qi]#./} (ffmpeg exit $rc)"
      failed+=("${queue_src[$qi]#./}")
    fi
  done

  banner "Queue finished:"
  echo "  completed $done_n / $total"
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "  failed:"
    for f in "${failed[@]}"; do
      echo "    $f"
    done
    exit 1
  fi
}
