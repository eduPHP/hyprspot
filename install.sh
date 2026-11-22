#!/usr/bin/env bash
# Liga o hyprspot ao Hyprland do usuário atual. Idempotente: reexecutar só atualiza.
#   ./install.sh            instala/atualiza
#   ./install.sh --remove   remove atalhos, autostart e links (mantém Soloist e tokens)
#
# Os atalhos ficam em ~/.config/hypr/hyprspot.conf (gerado). O hyprland.conf ganha só um
# bloco "# >>> hyprspot" / "# <<< hyprspot" com o `source`, e um backup .bak.hyprspot.<data>
# antes de qualquer mudança. Funciona com e sem Omarchy.
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
HYPR="$HOME/.config/hypr"
BIN="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
STAMP="$(date +%Y%m%d-%H%M%S)"
BEGIN="# >>> hyprspot (gerado por install.sh, não editar à mão)"
END="# <<< hyprspot"
MARK="player do hyprspot"

strip_block() {  # imprime o arquivo sem o bloco do hyprspot (e sem a linha em branco que o precede)
  awk -v b="# >>> hyprspot" -v e="$END" '
    skip { if (index($0, e) == 1) skip = 0; next }
    index($0, b) == 1 { if (pend > 0) pend--; for (; pend > 0; pend--) print ""; skip = 1; next }
    $0 == "" { pend++; next }
    { for (; pend > 0; pend--) print ""; print }
    END { for (; pend > 0; pend--) print "" }' "$1"
}

put_block() {  # put_block <arquivo> <conteúdo|vazio para remover>
  local file="$1" content="$2" tmp
  if [ ! -f "$file" ]; then
    [ -n "$content" ] || return 0
    touch "$file"
  fi
  tmp="$(mktemp)"
  strip_block "$file" > "$tmp"
  if [ -n "$content" ]; then
    printf '\n%s\n%s\n%s\n' "$BEGIN" "$content" "$END" >> "$tmp"
  fi
  if ! cmp -s "$tmp" "$file"; then
    cp -p "$file" "$file.bak.hyprspot.$STAMP"
    cat "$tmp" > "$file"
    echo "  atualizado: $file (backup: $(basename "$file").bak.hyprspot.$STAMP)"
  fi
  rm -f "$tmp"
}

remove_legacy_blocks() {  # versões antigas punham blocos direto nos arquivos do Omarchy
  local f
  for f in bindings.conf autostart.conf looknfeel.conf; do
    if [ -f "$HYPR/$f" ] && grep -q "^# >>> hyprspot" "$HYPR/$f"; then put_block "$HYPR/$f" ""; fi
  done
}

hypr_conf() {
  cat <<EOF
# hyprspot — gerado por $ROOT/install.sh; reexecute-o em vez de editar.
bindd = SUPER ALT, M, hyprspot, exec, $BIN/hyprspot-ctl toggle
bindd = SUPER ALT, B, hyprspot: buscar, exec, $BIN/hyprspot-ctl expand-search
bindd = SUPER ALT, H, hyprspot: esconder/mostrar, exec, $BIN/hyprspot-ctl hide-toggle

exec-once = $BIN/hyprspot
layerrule = match:namespace hyprspot, blur on, ignore_alpha 0.2, no_anim on
windowrule = match:class ^(dev\.hyprspot\.Setup)\$, float on
windowrule = match:class ^(dev\.hyprspot\.Setup)\$, size 580 600
EOF
}

if [ "${1:-}" = "--remove" ]; then
  echo "Removendo o hyprspot do Hyprland…"
  put_block "$HYPR/hyprland.conf" ""
  remove_legacy_blocks
  rm -f "$HYPR/hyprspot.conf" "$BIN/hyprspot" "$BIN/hyprspot-ctl"
  hyprctl reload >/dev/null 2>&1 || true
  echo "Pronto. O Soloist (soloist.service) e os tokens em ~/.config/hyprspot foram mantidos."
  exit 0
fi

echo "Instalando o hyprspot…"
if [ ! -f "$HYPR/hyprland.conf" ]; then
  echo "ERRO: não encontrei $HYPR/hyprland.conf. O hyprspot precisa do Hyprland configurado." >&2
  exit 1
fi

mkdir -p "$BIN"
ln -sfn "$ROOT/bin/hyprspot" "$BIN/hyprspot"
ln -sfn "$ROOT/bin/hyprspot-ctl" "$BIN/hyprspot-ctl"
echo "  links: $BIN/hyprspot, $BIN/hyprspot-ctl"

remove_legacy_blocks
tmp="$(mktemp)"
hypr_conf > "$tmp"
if ! cmp -s "$tmp" "$HYPR/hyprspot.conf" 2>/dev/null; then
  cat "$tmp" > "$HYPR/hyprspot.conf"
  echo "  atualizado: $HYPR/hyprspot.conf"
fi
rm -f "$tmp"
put_block "$HYPR/hyprland.conf" "source = $HYPR/hyprspot.conf"

# Serviço do Soloist: instala o nosso, sem sobrescrever um serviço feito pelo usuário.
mkdir -p "$UNIT_DIR"
UNIT="$UNIT_DIR/soloist.service"
if [ ! -f "$UNIT" ] || { grep -q "$MARK" "$UNIT" && ! cmp -s "$ROOT/share/soloist.service" "$UNIT"; }; then
  cp "$ROOT/share/soloist.service" "$UNIT"
  echo "  serviço: $UNIT"
elif ! grep -q "$MARK" "$UNIT"; then
  echo "  mantido: $UNIT (não foi criado pelo hyprspot)"
fi
systemctl --user daemon-reload >/dev/null 2>&1 || true
systemctl --user enable soloist.service >/dev/null 2>&1 || true
if [ -f "$HOME/.config/hyprspot/soloist.env" ]; then
  # start não reinicia um Soloist que já está tocando
  systemctl --user start soloist.service >/dev/null 2>&1 || true
  echo "  soloist.service habilitado"
else
  echo "  soloist.service habilitado (sobe quando a chave for salva no 'hyprspot setup')"
fi

# Só remove o spotifyd.service que o próprio hyprspot criou na fase de testes (marcado na Description).
SPD="$UNIT_DIR/spotifyd.service"
if [ -f "$SPD" ] && grep -q "$MARK" "$SPD"; then
  systemctl --user disable --now spotifyd.service >/dev/null 2>&1 || true
  rm -f "$SPD"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  echo "  spotifyd.service do hyprspot removido (o binário ~/.local/bin/spotifyd foi mantido)"
fi

hyprctl reload >/dev/null 2>&1 && echo "  Hyprland recarregado" || true
echo "Pronto."
