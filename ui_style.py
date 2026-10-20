"""Visual da UI v2: tokens, CSS, JS, avatares e textos de ajuda "(?)".

Segue design_spec.md. Notas de Gradio 6.30:
- o CSS vai em `launch(css=...)` (em `Blocks(css=...)` ha aviso);
- o modo escuro e `body.dark`, nao `prefers-color-scheme`;
- seletores so por elem_id/elem_classes proprios e por elementos semanticos.
Nenhum caminho absoluto: os PNGs sao lidos de ./assets relativo a este arquivo.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import i18n

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
AVATAR_FILES = [f"pessoa_{i}.png" for i in range(1, 7)]
AVATAR_LABELS = [f"Avatar {i}" for i in range(1, 7)]
N_AVATARS = len(AVATAR_FILES)
DEFAULT_CLAIR_AVATAR = 1  # indice 0-based: pessoa_2.png
N_PEOPLE = 6
DEFAULT_PERSON_AVATARS = [0, 2, 3, 4, 5, 1]  # Pessoa1..6 = avatares 1, 3, 4, 5, 6, 2 (o 2 tambem e o padrao da Clair)
AVATAR_PX = 168  # os PNGs (256 px) sao reduzidos so no CSS embutido; o maior uso e 84 px (2x)

# ------------------------------------------------------------------ avatares
def svg_uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def system_avatar() -> str:
    return svg_uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="32" fill="#777"/>'
                   '<rect x="29" y="14" width="6" height="22" rx="3" fill="#fff"/><circle cx="32" cy="46" r="4" fill="#fff"/></svg>')


_PNG_CACHE: list[str] | None = None


def _placeholder(i: int) -> str:
    """Avatar neutro (silhueta) usado quando o PNG nao existe: a UI nao quebra."""
    return svg_uri('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="32" fill="#9aa5a0"/>'
                   '<circle cx="32" cy="25" r="11" fill="#fff" opacity=".9"/><path d="M12 58c3-13 13-19 20-19s17 6 20 19z" fill="#fff" opacity=".9"/></svg>')


def _png_uri(path: Path) -> str:
    """Data URI do avatar para o CSS embutido: WebP 168 px (~6 KB; o PNG reduzido tinha ~30 KB). Sem Pillow/WebP, usa o PNG original."""
    data = path.read_bytes()
    mime = "image/png"
    try:  # Pillow vem junto com o Gradio
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        if max(im.size) > AVATAR_PX:
            im.thumbnail((AVATAR_PX, AVATAR_PX), Image.LANCZOS)
        buf = io.BytesIO()
        try:
            im.save(buf, "WEBP", quality=82, method=6)
            mime = "image/webp"
        except Exception:
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
    except Exception:
        pass
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def avatar_uris() -> list[str]:
    """Data URIs dos 6 avatares (assets/pessoa_N.png). Ausente ou ilegivel -> silhueta neutra."""
    global _PNG_CACHE
    if _PNG_CACHE is None:
        out = []
        for i, f in enumerate(AVATAR_FILES):
            try:
                out.append(_png_uri(ASSETS / f))
            except OSError:
                out.append(_placeholder(i))
        _PNG_CACHE = out
    return _PNG_CACHE


def _idx(v, default: int) -> int:
    try:
        return min(max(int(v), 0), N_AVATARS - 1)
    except (TypeError, ValueError):
        return default


def dyn_style(person_avs: list, clair_idx) -> str:
    """<style> dinamico: miniaturas de quem fala (avatar de cada pessoa) e avatar atual da Clair."""
    rules = []
    for i in range(N_PEOPLE):
        k = _idx(person_avs[i] if i < len(person_avs) else None, DEFAULT_PERSON_AVATARS[i])
        rules.append(f"#cl-who label:nth-of-type({i + 1}) input{{background-image:var(--cl-av-{k + 1})}}")
    rules.append(f"body{{--cl-clair-img:var(--cl-av-{_idx(clair_idx, DEFAULT_CLAIR_AVATAR) + 1})}}")
    return "<style>" + "\n".join(rules) + "</style>"


# -------------------------------------------------------------- textos de ajuda
# Fonte: locales/<lang>.json (chaves help.<k>.label/.info/.detail). HELP = versao PT: chave -> (rotulo, info, detalhe).
HELP_KEYS = ["ls", "active", "mode", "lang", "kw", "avatar", "group", "people", "pull", "secs", "tech"]


def help_texts(key: str, lang: str = "pt") -> tuple[str, str, str]:
    return (i18n.t(f"help.{key}.label", lang), i18n.t(f"help.{key}.info", lang), i18n.t(f"help.{key}.detail", lang))


HELP: dict[str, tuple[str, str, str]] = {k: help_texts(k, "pt") for k in HELP_KEYS}

_TBC = i18n.TBC_RE


def tbc_markup(escaped: str) -> str:
    """Envolve marcadores '[a confirmar ...]' (ja escapados) em <em>, sem alterar o texto."""
    return _TBC.sub(r"<em>\1</em>", escaped)


def help_html(key: str, lang: str = "pt") -> str:
    label, _info, detail = help_texts(key, lang)
    tip = tbc_markup(html.escape(detail))
    aria = html.escape(i18n.t("help.aria", lang, label=label))
    return (f'<span class="cl-hlp"><button type="button" aria-label="{aria}" '
            f'aria-describedby="cl-tip-{key}">?</button>'
            f'<span role="tooltip" id="cl-tip-{key}"><span class="tipbox">{tip}</span></span></span>')


# ------------------------------------------------------------------------ JS
# Idioma do site: escolha inicial (localStorage -> navigator.language -> pt) e persistencia por visitante (so comodidade).
LANG_PICK_JS = """
(...a) => {
  const ok = ['pt', 'en', 'es', 'nl', 'de', 'zh'];
  let v = null;
  try { v = localStorage.getItem('cl-lang'); } catch (e) {}
  if (!ok.includes(v)) {
    const n = ((navigator.languages && navigator.languages[0]) || navigator.language || 'pt').toLowerCase().split('-')[0];
    v = ok.includes(n) ? n : 'pt';
  }
  a[0] = v;
  return a;
}
"""
LANG_SAVE_JS = """
(...a) => {
  try { localStorage.setItem('cl-lang', a[0]); } catch (e) {}
  return a;
}
"""
LANG_SYNC_JS = """
() => {
  const b = document.querySelector('#cl-brand .cl-brandrow');
  if (b) { document.documentElement.lang = b.dataset.htmllang || 'pt-BR'; document.title = b.dataset.title || document.title; }
}
"""

JS = """
() => {
  if (window.__clInit) return;
  window.__clInit = true;
  const closeAll = (except) => document.querySelectorAll('.cl-hlp.open').forEach(h => { if (h !== except) h.classList.remove('open'); });
  document.addEventListener('click', (e) => {
    const b = e.target.closest ? e.target.closest('.cl-hlp > button') : null;
    closeAll(b ? b.parentElement : null);
    if (b) b.parentElement.classList.toggle('open');
    const c = e.target.closest ? e.target.closest('[data-cl-close]') : null;
    if (c) { const t = c.closest('.cl-err'); if (t) t.remove(); }
  });
  // altura do chat = viewport visivel (visualViewport acompanha o teclado virtual) - topo do chat - rodape
  const fit = () => {
    const c = document.getElementById('cl-chat');
    if (!c || !c.offsetParent) return;
    c.style.removeProperty('--cl-chat-h');
    const top = c.getBoundingClientRect().top + window.scrollY;
    const f = document.querySelector('footer');
    // o rodape do Gradio fica fora do escopo do CSS customizado: esconde por JS quando o viewport e baixo
    if (f) f.style.display = (window.visualViewport ? window.visualViewport.height : window.innerHeight) <= 560 ? 'none' : '';
    const foot = (f && f.offsetParent ? f.getBoundingClientRect().height + 32 : 16);
    const vh = (window.visualViewport ? window.visualViewport.height : window.innerHeight);
    document.documentElement.style.setProperty('--cl-chat-h', Math.max(200, Math.floor(vh - top - foot)) + 'px');
  };
  // .gradio-container vem com overflow:hidden, o que cria um 'scroll container' e quebra o position:sticky da barra Salvar
  // (fora do escopo do CSS customizado: ajusta por JS; 'clip' corta o excesso sem criar scroll container)
  const gc = document.querySelector('.gradio-container');
  if (gc) { gc.style.overflowX = 'clip'; gc.style.overflowY = 'visible'; }
  let ft = 0;
  const fitSoon = () => { clearTimeout(ft); ft = setTimeout(fit, 60); };
  window.addEventListener('resize', fitSoon);
  window.addEventListener('orientationchange', fitSoon);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', fitSoon);
  document.addEventListener('click', () => setTimeout(fit, 250));
  document.addEventListener('focusin', (e) => {
    if (e.target && e.target.closest && e.target.closest('#cl-composer')) setTimeout(() => { fit(); window.scrollTo(0, 0); }, 300);
  });
  setTimeout(fit, 400);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeAll(null);
      const a = document.activeElement;
      if (a && a.closest && a.closest('.cl-hlp')) a.blur();
    }
  });
}
"""

# ----------------------------------------------------------------------- CSS
_CSS_TEMPLATE = r"""
/* ===== Tokens (--cl-*, com fallback as variaveis do Gradio) ===== */
/* declarados em body (nao :root) para herdar as variaveis do tema escuro, que o Gradio define em body.dark */
body{
  --cl-font: var(--font, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif);
  --cl-mono: var(--font-mono, ui-monospace, Menlo, Consolas, monospace);
  --cl-bg: var(--body-background-fill, #fff);
  --cl-surface: var(--background-fill-secondary, #fafafa);
  --cl-surface-2:#f4f4f5;
  --cl-text: var(--body-text-color, #27272a);
  --cl-text-2:#52525b;
  --cl-muted:#686872;
  --cl-border: var(--border-color-primary, #e4e4e7);
  --cl-border-strong:#8b8b95;
  --cl-accent: var(--color-accent, #f97316);
  --cl-accent-strong:#c2410c;
  --cl-accent-hover:#9a3412;
  --cl-accent-soft:#fff1e6;
  --cl-focus:#c2410c;
  --cl-ok:#1d6a50;
  --cl-clair-bg:#e6f5ee; --cl-clair-border:#b9ddca; --cl-clair-text:#14302a; --cl-clair-tag:#1d6a50;
  --cl-person-bg:#f1f1f3;
  --cl-danger-bg:#fef2f2; --cl-danger-border:#fecaca; --cl-danger-text:#991b1b;
  --cl-shadow:0 6px 24px rgba(0,0,0,.12);
  --cl-r-sm:8px; --cl-r-md:12px; --cl-r-lg:18px;
  --cl-s2:8px; --cl-s3:12px; --cl-s4:16px; --cl-s5:24px;
  --cl-col:780px;
  /* altura ocupada por tudo que NAO e o chat (marca + abas + rodape do Gradio + respiros): ajustar aqui */
  --cl-chrome:172px;
  @@AVVARS@@
  --cl-clair-img:var(--cl-av-2);
}
body.dark{
  --cl-surface-2:#222226; --cl-text-2:#d4d4d8; --cl-muted:#a1a1aa;
  --cl-border-strong:#71717a; --cl-focus:#fdba74; --cl-accent-soft:#2a1a0f; --cl-ok:#7fd9b8;
  --cl-clair-bg:#15302a; --cl-clair-border:#2a5a4b; --cl-clair-text:#e0f4ec; --cl-clair-tag:#7fd9b8;
  --cl-person-bg:#232327;
  --cl-danger-bg:#2a1212; --cl-danger-border:#6b2222; --cl-danger-text:#fecaca;
  --cl-shadow:0 6px 24px rgba(0,0,0,.5);
}

.gradio-container .main{padding-left:0!important;padding-right:0!important}
.gradio-container{max-width:100%!important;padding-left:0!important;padding-right:0!important}
input,textarea{scroll-margin-block:16px 120px}
.gradio-container :focus-visible{outline:2px solid var(--cl-focus);outline-offset:2px}
.cl-sr{position:absolute!important;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}

/* ===== Graficos (PNG claro + escuro; alternados por variaveis, pitfall G1) ===== */
body{--v-lt-d:block;--v-dk-d:none}
body.dark{--v-lt-d:none;--v-dk-d:block}
.cl-plots h2{margin:0 0 var(--cl-s2);font-size:1.1rem}
.cl-plots-slot{min-height:120px;display:flex;align-items:center;justify-content:center;color:var(--cl-muted);text-align:center;padding:var(--cl-s4);border:1px dashed var(--cl-border-strong);border-radius:var(--cl-r-md)}
.v-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:var(--cl-s2);margin:var(--cl-s3) 0}
.v-kpi{background:var(--cl-surface-2);border:1px solid var(--cl-border);border-radius:var(--cl-r-md);padding:var(--cl-s2) var(--cl-s3);display:flex;flex-direction:column}
.v-kpi b{font-size:1.4rem;line-height:1.2}
.v-kpi span{color:var(--cl-muted);font-size:.8rem}
.v-warn{background:var(--cl-accent-soft);border:1px solid var(--cl-border-strong);border-radius:var(--cl-r-md);padding:var(--cl-s2) var(--cl-s3);margin:var(--cl-s2) 0}
.v-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:var(--cl-s4)}
.v-card{margin:0;background:var(--cl-surface);border:1px solid var(--cl-border);border-radius:var(--cl-r-md);padding:var(--cl-s3);min-width:0}
.v-card figcaption h3{margin:0;font-size:1rem}
.v-card figcaption p{margin:2px 0 var(--cl-s2);color:var(--cl-muted);font-size:.85rem}
.v-card .v-img{max-width:100%;height:auto;border-radius:var(--cl-r-sm,6px)}
.v-card .v-lt{display:var(--v-lt-d)}
.v-card .v-dk{display:var(--v-dk-d)}
.v-tblwrap{overflow-x:auto;max-width:100%}
.v-tblwrap table{border-collapse:collapse;font-size:.85rem}
.v-tblwrap th,.v-tblwrap td{border-bottom:1px solid var(--cl-border);padding:4px 8px;text-align:left;white-space:nowrap}
.v-json pre{white-space:pre-wrap;font-size:.75rem;max-height:320px;overflow:auto;margin:0}
.v-card .q{color:var(--cl-muted);font-size:.8rem;margin:2px 0 6px}
.v-none{color:var(--cl-muted);min-height:80px;display:flex;align-items:center}

/* ===== Cabecalho: marca + seletor de idioma ===== */
#cl-head{display:flex!important;flex-wrap:nowrap!important;align-items:center!important;gap:var(--cl-s2)!important;padding:0!important;border:0!important;background:none!important}
#cl-head>#cl-brand{flex:1 1 auto!important;min-width:0!important}
#cl-head>#cl-lang{flex:0 0 auto!important;width:104px!important;min-width:104px!important;margin-right:var(--cl-s3)!important;padding:0!important;border:0!important}
#cl-lang input,#cl-lang .wrap{min-height:44px;font-size:.9rem;font-weight:600}
#cl-lang .wrap-inner,#cl-lang .secondary-wrap{min-height:44px}
/* fonte CJK: pilha com familias comuns do sistema e fallback generico */
html[lang^="zh"] body,html[lang^="zh"] .gradio-container{
  --font:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei","WenQuanYi Micro Hei",system-ui,sans-serif;
  --cl-font:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei","WenQuanYi Micro Hei",system-ui,sans-serif}
html[lang^="zh"] body,html[lang^="zh"] button,html[lang^="zh"] input,html[lang^="zh"] textarea,html[lang^="zh"] select{
  font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei","WenQuanYi Micro Hei",system-ui,sans-serif}

/* textos mais longos (de, nl, es...): botoes da barra e Novo grupo quebram linha em vez de estourar */
#cl-savebar .cl-btnrow>button,#cl-newgrp-cfg{white-space:normal!important;line-height:1.15;min-height:44px;overflow-wrap:anywhere}
#cl-savebar .cl-btnrow{flex-wrap:nowrap!important}
#cl-savebar .cl-btnrow>button{flex:1 1 auto!important;min-width:0!important}
#cl-chat-head{flex-wrap:wrap!important}

/* ===== Marca e abas principais ===== */
#cl-brand{padding:0!important;border:0!important;background:none!important;min-height:0!important}
#cl-dyn{display:none!important}
#cl-brand{margin:0!important;min-height:0!important;height:auto!important}
#cl-brand .html-container,#cl-brand .prose{padding:0!important;min-height:0!important}
#cl-brand .cl-brandrow{display:flex;align-items:center;gap:var(--cl-s2);padding:8px var(--cl-s4) 0;line-height:1.2;font-weight:650;color:var(--cl-text)}
#cl-brand .cl-brandrow small{font-weight:400;color:var(--cl-muted);margin-left:2px}
.cl-clairimg,.cl-avimg{display:block;flex:none;border-radius:50%;background-size:cover;background-position:center;background-color:var(--cl-surface-2);box-shadow:0 0 0 1px var(--cl-border)}
.cl-clairimg{background-image:var(--cl-clair-img);background-color:var(--cl-clair-bg)}
@@AVCLASSES@@
.cl-brand-av{width:30px;height:30px}
#cl-main>.tab-wrapper [role=tablist]{padding:0 var(--cl-s2)}
#cl-main>.tab-wrapper [role=tab]{font-weight:550;color:var(--cl-text-2);min-height:44px;padding:0 var(--cl-s3)}
#cl-main>.tab-wrapper [role=tab][aria-selected=true]{color:var(--cl-text);border-bottom-color:var(--cl-accent)!important}
.cl-col{width:100%;max-width:var(--cl-col);margin:0 auto;padding:0 var(--cl-s4)!important;box-sizing:border-box}
.cl-banner{background:var(--cl-danger-bg)!important;border:1px solid var(--cl-danger-border)!important;color:var(--cl-danger-text);
  border-radius:var(--cl-r-sm);padding:8px 12px!important;font-size:.88rem;margin:var(--cl-s2) var(--cl-s4)!important;width:auto!important}
.cl-banner.empty-banner{display:none}

/* ===== Configuracao ===== */
#cl-cfgintro{padding:var(--cl-s4) 0 0!important;border:0!important;background:none!important}
.cl-page-title{font-size:1.4rem;line-height:1.25;margin:0 0 4px;font-weight:700;color:var(--cl-text)}
.cl-lede{margin:0 0 var(--cl-s2);color:var(--cl-text-2)}
.cl-note{color:var(--cl-text-2);font-size:.88rem;border-left:3px solid var(--cl-border-strong);padding:2px 0 2px 10px;margin:0 0 var(--cl-s3)}
.cl-note.warn{border-left-color:var(--cl-accent-strong)}
#cl-notice{padding:0!important;border:0!important;background:none!important;min-height:0!important}
#cl-sub>.tab-wrapper [role=tablist]{gap:6px;padding-bottom:var(--cl-s3);border-bottom:0!important;flex-wrap:wrap}
#cl-subanalytics>.tab-wrapper [role=tablist]{gap:6px;padding-bottom:var(--cl-s3);border-bottom:0!important;flex-wrap:wrap}
#cl-sub>.tab-wrapper [role=tab]{border:1px solid var(--cl-border)!important;border-radius:999px!important;padding:6px 14px!important;min-height:44px;display:inline-flex;align-items:center;color:var(--cl-text-2);font-size:.92rem;background:var(--cl-bg)}
#cl-subanalytics>.tab-wrapper [role=tab]{border:1px solid var(--cl-border)!important;border-radius:999px!important;padding:6px 14px!important;min-height:44px;display:inline-flex;align-items:center;color:var(--cl-text-2);font-size:.92rem;background:var(--cl-bg)}
#cl-sub>.tab-wrapper [role=tab][aria-selected=true]{background:var(--cl-text)!important;color:var(--cl-bg)!important;border-color:var(--cl-text)!important}
#cl-subanalytics>.tab-wrapper [role=tab][aria-selected=true]{background:var(--cl-text)!important;color:var(--cl-bg)!important;border-color:var(--cl-text)!important}
#cl-sub>.tab-wrapper,#cl-sub>.tab-wrapper .tab-container{height:auto!important;min-height:44px;overflow:visible!important}
#cl-subanalytics>.tab-wrapper,#cl-subanalytics>.tab-wrapper .tab-container{height:auto!important;min-height:44px;overflow:visible!important}
#cl-sub>.tab-wrapper [role=tab]::after,#cl-sub>.tab-wrapper [role=tab]::before{display:none!important}
#cl-subanalytics>.tab-wrapper [role=tab]::after,#cl-subanalytics>.tab-wrapper [role=tab]::before{display:none!important}
.cl-card{background:var(--cl-surface)!important;border:1px solid var(--cl-border)!important;border-radius:var(--cl-r-md)!important;
  padding:var(--cl-s4)!important;margin-bottom:var(--cl-s4)!important;gap:var(--cl-s4)!important;box-shadow:none!important}
.cl-card .form{background:none!important;border:0!important;box-shadow:none!important;gap:var(--cl-s4)!important}
.cl-card>.block.hide-container,.cl-card .cl-h2{padding:0!important}
.cl-card h2{font-size:1rem;font-weight:650;margin:0;color:var(--cl-text);border:0;padding:0}
.cl-card h2 small{color:var(--cl-muted);font-weight:500;font-size:.85rem;margin-left:6px}
.cl-field{position:relative;gap:0!important}
.cl-field .info-text{color:var(--cl-muted)!important;font-size:.85rem!important;line-height:1.4;padding-right:34px}
.cl-field [data-testid=block-info],.cl-field label>span[data-testid=block-info],.cl-field .label-text{font-weight:600!important;font-size:.95rem!important;color:var(--cl-text)}
.cl-field .block,.cl-field .form>.block{background:none!important;box-shadow:none!important;border:0!important}
.cl-field input[type=text],.cl-field input[type=number],.cl-field textarea{background:var(--cl-bg)!important}
.cl-field>.cl-helpwrap{position:absolute!important;top:0;right:0;z-index:6;width:auto!important;min-width:0!important;
  border:0!important;background:none!important;padding:0!important;overflow:visible!important;box-shadow:none!important}
/* "(?)" */
.cl-hlp{display:inline-block;position:relative}
.cl-hlp>button{width:24px;height:24px;border-radius:50%;border:1px solid var(--cl-border-strong);background:var(--cl-bg);
  color:var(--cl-text-2);font-size:.78rem;font-weight:700;line-height:1;cursor:help;padding:0}
.cl-hlp>button:hover{border-color:var(--cl-text-2)}
.cl-hlp>button::after{content:"";position:absolute;inset:-10px}
.cl-hlp>button{position:relative}
.cl-hlp [role=tooltip]{position:absolute;right:-4px;top:100%;z-index:30;width:min(330px,calc(100vw - 96px));padding-top:8px;visibility:hidden;opacity:0;transition:opacity .12s}
.cl-hlp .tipbox{display:block;background:var(--cl-text);color:var(--cl-bg);border-radius:var(--cl-r-sm);padding:10px 12px;font-size:.84rem;
  line-height:1.45;box-shadow:var(--cl-shadow);text-align:left;font-weight:400}
.cl-hlp .tipbox em{font-style:normal;opacity:.75}
.cl-hlp:hover [role=tooltip],.cl-hlp:focus-within [role=tooltip],.cl-hlp.open [role=tooltip]{visibility:visible;opacity:1}
/* interruptor */
.cl-switch input[type=checkbox]{appearance:none;-webkit-appearance:none;width:44px!important;height:26px!important;border-radius:999px!important;
  background:var(--cl-border-strong)!important;position:relative;cursor:pointer;border:0!important;transition:background .15s;flex:none;box-shadow:none!important}
.cl-switch input[type=checkbox]::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:transform .15s;box-shadow:0 1px 2px rgba(0,0,0,.3)}
.cl-switch input[type=checkbox]:checked{background:var(--cl-accent-strong)!important}
.cl-switch input[type=checkbox]:checked::after{transform:translateX(18px)}
.cl-switch input[type=checkbox]:checked::before{display:none!important}
.cl-switch label{gap:var(--cl-s3)!important;min-height:32px;align-items:center;padding-right:34px!important}
.cl-switch label span{white-space:normal!important;min-width:0}
/* guia de modos */
#cl-guide-acc{border:1px solid var(--cl-border)!important;border-radius:var(--cl-r-sm)!important;background:var(--cl-bg)!important;margin-top:var(--cl-s3)}
.cl-guide{font-size:.9rem;color:var(--cl-text-2)}
.cl-guide h3{margin:0 0 6px;font-size:.95rem;color:var(--cl-text);display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.cl-guide .gen{margin:0 0 var(--cl-s3)}
.cl-modes{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:var(--cl-s3)}
.cl-mode{border:1px solid var(--cl-border);border-radius:var(--cl-r-sm);padding:var(--cl-s3);background:var(--cl-surface);min-width:0}
.cl-mode.cur{border-color:var(--cl-accent-strong);box-shadow:inset 0 0 0 1px var(--cl-accent-strong)}
.cl-mode p{margin:0 0 6px;font-size:.85rem;line-height:1.4}
.cl-mode b{color:var(--cl-text)}
.cl-mode ul{margin:0 0 6px;padding-left:18px;font-size:.85rem}
.cl-mode details{margin-top:6px}
.cl-mode summary{cursor:pointer;font-weight:600;font-size:.85rem;color:var(--cl-text)}
.cl-mode .ex{color:var(--cl-muted);font-size:.78rem}
.cl-badge{font-size:.7rem;font-weight:600;border-radius:999px;padding:1px 8px;border:1px solid var(--cl-border-strong);color:var(--cl-text-2);white-space:nowrap}
.cl-badge.cur{background:var(--cl-accent-strong);border-color:var(--cl-accent-strong);color:#fff}
.cl-tscroll{overflow-x:auto;border:1px solid var(--cl-border);border-radius:var(--cl-r-sm);background:var(--cl-bg);margin-top:var(--cl-s3)}
.cl-tscroll table{width:100%;border-collapse:collapse;font-size:.82rem}
.cl-tscroll th,.cl-tscroll td{border-bottom:1px solid var(--cl-border);padding:6px 8px;text-align:left;white-space:nowrap}
.cl-guide details.src{margin-top:var(--cl-s3);font-size:.82rem;color:var(--cl-muted)}
.cl-guide em.tbc{font-style:normal;background:var(--cl-accent-soft);color:var(--cl-accent-strong);border-radius:4px;padding:0 3px}
/* avatar da Clair (Radio estilizado) */
#cl-avatar .wrap{display:grid!important;grid-template-columns:repeat(3,1fr);gap:var(--cl-s3)}
#cl-avatar label{display:flex!important;flex-direction:column;align-items:center;gap:6px;padding:var(--cl-s3) var(--cl-s2)!important;border:2px solid var(--cl-border)!important;
  border-radius:var(--cl-r-md)!important;background:var(--cl-bg)!important;cursor:pointer;text-align:center;box-shadow:none!important;position:relative}
#cl-avatar label:hover{border-color:var(--cl-border-strong)!important}
#cl-avatar label:has(input:checked){border-color:var(--cl-accent-strong)!important;background:var(--cl-accent-soft)!important}
#cl-avatar input{appearance:none;-webkit-appearance:none;width:76px!important;height:76px!important;border-radius:50%!important;border:3px solid var(--cl-border)!important;
  background-size:cover!important;background-position:center!important;background-color:var(--cl-surface-2)!important;margin:0!important;cursor:pointer;flex:none}
@@AVRADIO@@
#cl-avatar input:checked{border-color:var(--cl-accent-strong)!important;box-shadow:0 0 0 3px var(--cl-bg),0 0 0 5px var(--cl-accent-strong)!important}
#cl-avatar span{font-size:.85rem;font-weight:600;color:var(--cl-text)}
#cl-avatar input:checked + span{font-weight:800;color:var(--cl-accent-strong)}
/* participantes */
.cl-flabel{padding-right:34px}
.cl-flabel b{font-weight:600;font-size:.95rem;color:var(--cl-text)}
.cl-hint{color:var(--cl-muted);font-size:.85rem;line-height:1.4}
.cl-people{display:grid!important;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:var(--cl-s3)!important;margin-top:6px}
.cl-people>*{min-width:0!important}
.cl-person{gap:6px!important;min-width:0}
.cl-person .block{min-width:0!important;background:none!important;border:0!important;padding:0!important;box-shadow:none!important}
.cl-person input[type=text]{background:var(--cl-bg)!important;border:1px solid var(--cl-border-strong)!important;border-radius:var(--cl-r-sm)!important;padding:8px 10px!important;min-height:40px;box-sizing:border-box}
.cl-pav .wrap{display:flex!important;flex-wrap:nowrap;gap:4px!important}
.cl-pav label{display:flex!important;background:none!important;border:0!important;box-shadow:none!important;padding:3px!important;min-height:0!important;cursor:pointer;flex:none;width:auto!important}
.cl-pav input{appearance:none;-webkit-appearance:none;width:30px!important;height:30px!important;border-radius:50%!important;border:0!important;margin:0!important;cursor:pointer;
  background-size:cover!important;background-position:center!important;background-color:var(--cl-surface-2)!important;opacity:.7;box-shadow:0 0 0 1px var(--cl-border)!important}
.cl-pav input:hover{opacity:1}
.cl-pav input:checked{opacity:1;box-shadow:0 0 0 2px var(--cl-bg),0 0 0 4px var(--cl-accent-strong)!important}
.cl-pav input:focus-visible{box-shadow:0 0 0 2px var(--cl-bg),0 0 0 5px var(--cl-text)!important}
.cl-pav span,.cl-pav .info-text,.cl-pav [data-testid=block-info]{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
#cl-avwarn{padding:0!important;border:0!important;background:none!important;min-height:0!important}
.cl-pullrow{align-items:flex-start!important;gap:var(--cl-s4)!important}
.cl-pullrow .form>.block,.cl-pullrow>.block{background:none!important;box-shadow:none!important;border:0!important;padding-left:0!important}
.cl-pullrow .info-text{color:var(--cl-muted)!important;font-size:.85rem!important}
.cl-divider{height:1px;background:var(--cl-border);margin:0}
/* barra de salvar: ver bloco 'duas colunas' no fim */
.cl-status{display:flex;align-items:center;gap:8px;color:var(--cl-text-2);font-size:.9rem}
.cl-status .dot{width:9px;height:9px;border-radius:50%;background:var(--cl-ok);flex:none}
.cl-status.off .dot{background:var(--cl-border-strong)}
.cl-status.err{color:var(--cl-danger-text)}
.cl-status.err .dot{background:var(--cl-danger-text)}
.cl-status .warn{color:var(--cl-muted);display:block;font-size:.82rem}
/* tabelas/JSON das sub-abas: rolagem horizontal propria */
#cl-sub .table-wrap,#cl-sub .dataframe,#cl-sub [data-testid=dataframe]{max-width:100%;overflow-x:auto}
#cl-subanalytics .table-wrap,#cl-subanalytics .dataframe,#cl-subanalytics [data-testid=dataframe]{max-width:100%;overflow-x:auto}
#cl-sub .json-holder,#cl-sub .block pre{max-width:100%;overflow-x:auto}
#cl-subanalytics .json-holder,#cl-subanalytics .block pre{max-width:100%;overflow-x:auto}

/* ===== Chat ===== */
#cl-chat{display:flex!important;flex-direction:column;height:var(--cl-chat-h,calc(100dvh - var(--cl-chrome)));min-height:200px;gap:0!important;padding:0!important}
#cl-chat-head{flex:none!important;align-items:center!important;flex-wrap:nowrap!important;gap:var(--cl-s2)!important;border-bottom:1px solid var(--cl-border);
  padding:0 var(--cl-s4)!important;min-height:48px;max-width:var(--cl-col);width:100%;margin:0 auto;box-sizing:border-box}
#cl-chat-head>.block{border:0!important;padding:0!important;background:none!important;min-width:0!important}
#cl-chat-head>.block.cl-statuswrap{flex:1 1 auto!important}
.cl-statusline{display:flex;flex-wrap:wrap;align-items:center;gap:2px 8px;font-size:.84rem;color:var(--cl-muted);padding:6px 0}
.cl-statusline b{color:var(--cl-text-2);font-weight:600}
.cl-statusline .sep{opacity:.6}
.cl-statusline .live{display:inline-flex;align-items:center;gap:5px}
.cl-statusline .live::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--cl-ok)}
.cl-statusline .live.off::before{background:var(--cl-border-strong)}
.cl-statusline .live.warn::before{background:var(--cl-accent-strong)}
.cl-statusline .live.bad::before{background:var(--cl-danger-text)}
.cl-statusline .note{flex-basis:100%;color:var(--cl-danger-text)}
#cl-chat-head button.cl-linkbtn{background:none!important;border:0!important;box-shadow:none!important;padding:0 4px!important;min-width:0!important;min-height:32px;
  font-size:.84rem!important;color:var(--cl-text)!important;text-decoration:underline;text-underline-offset:2px;font-weight:400!important;flex:0 0 auto!important;width:auto!important}
#cl-chat-head button.cl-linkbtn:hover{color:var(--cl-accent-strong)!important}
#cl-chat-head button.cl-clear{flex:0 0 auto!important;min-width:44px!important;width:auto!important;min-height:44px;background:none!important;border:1px solid transparent!important;
  color:var(--cl-text-2)!important;font-size:.85rem!important;padding:0 8px!important;box-shadow:none!important}
#cl-grouprow{align-items:flex-end!important;flex-wrap:nowrap!important;gap:8px!important}
#cl-grouprow button.cl-newgrp{flex:0 0 auto!important;width:auto!important;min-width:44px!important;min-height:44px;white-space:nowrap}
#cl-chat-head button.cl-clear:hover{background:var(--cl-surface-2)!important;border-color:var(--cl-border)!important}
#cl-history{flex:1 1 0!important;min-height:0!important;overflow-y:auto!important;overflow-x:hidden!important;display:flex!important;flex-direction:column-reverse;
  border:0!important;background:none!important;padding:0!important}
#cl-history>.html-container{flex:1 0 auto;display:flex;flex-direction:column;padding:0!important}
#cl-history .prose{flex:1;display:flex;flex-direction:column;max-width:none!important;padding:0!important}
.cl-hist{flex:1;width:100%;max-width:var(--cl-col);margin:0 auto;padding:var(--cl-s4);box-sizing:border-box;display:flex;flex-direction:column;gap:var(--cl-s3)}
.cl-msg{display:flex;gap:10px;align-items:flex-start}
.cl-msg>.cl-avimg,.cl-msg>.cl-clairimg{width:34px;height:34px;border-radius:50%;flex:none;margin-top:2px}
.cl-bubble{background:var(--cl-person-bg);border-radius:4px var(--cl-r-lg) var(--cl-r-lg) var(--cl-r-lg);padding:8px 14px;max-width:min(86%,620px);min-width:0;overflow-wrap:anywhere;color:var(--cl-text)}
.cl-msg.clair .cl-bubble{background:var(--cl-clair-bg);border:1px solid var(--cl-clair-border);color:var(--cl-clair-text);padding:7px 13px}
.cl-msg.pro .cl-bubble{border-style:dashed}
.cl-meta{font-size:.78rem;color:var(--cl-muted);margin-bottom:1px;display:flex;gap:6px;align-items:baseline;flex-wrap:wrap}
.cl-msg.clair .cl-meta{color:var(--cl-clair-tag)}
.cl-meta b{font-weight:650;color:var(--cl-text-2)}
.cl-msg.clair .cl-meta b{color:var(--cl-clair-text)}
.cl-txt{white-space:pre-wrap}
.cl-protag{display:inline-flex;align-items:center;gap:5px;font-size:.74rem;font-weight:600;color:var(--cl-clair-tag);margin-bottom:3px}
.cl-tech{margin-top:6px;font-size:.78rem;color:var(--cl-muted)}
.cl-tech .one{font-family:var(--cl-mono);font-size:.74rem;overflow-wrap:anywhere}
.cl-tech summary{cursor:pointer;margin-top:2px;width:max-content}
.cl-tech pre{margin:6px 0 0;padding:8px 10px;background:var(--cl-bg);border:1px solid var(--cl-border);border-radius:var(--cl-r-sm);font:.74rem/1.45 var(--cl-mono);
  color:var(--cl-text-2);white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto}
.cl-msg.clair .cl-tech{color:var(--cl-clair-tag)}
.cl-empty{margin:auto;text-align:center;padding:var(--cl-s5) var(--cl-s4);max-width:420px}
.cl-empty .cl-clairimg{width:84px;height:84px;margin:0 auto}
.cl-empty h2{margin:var(--cl-s3) 0 4px;font-size:1.15rem;color:var(--cl-text)}
.cl-empty p{margin:0;color:var(--cl-text-2)}
.cl-dots{display:inline-flex;gap:3px;margin-left:6px}
.cl-dots i{width:6px;height:6px;border-radius:50%;background:var(--cl-clair-tag);animation:clbl 1.2s infinite ease-in-out}
.cl-dots i:nth-child(2){animation-delay:.15s}.cl-dots i:nth-child(3){animation-delay:.3s}
@keyframes clbl{0%,60%,100%{opacity:.25;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}
@keyframes clpulse{0%,100%{box-shadow:0 0 0 0 rgba(42,160,120,0)}50%{box-shadow:0 0 0 4px rgba(42,160,120,.35)}}
@media (prefers-reduced-motion: reduce){.cl-dots i,.cl-clairmark.busy .cl-clairimg{animation:none;opacity:.8}*{transition:none!important}}
#cl-composer{flex:none!important;border-top:1px solid var(--cl-border);background:var(--cl-bg);padding:var(--cl-s3) var(--cl-s4) calc(var(--cl-s3) + env(safe-area-inset-bottom))!important;
  gap:var(--cl-s2)!important;max-width:var(--cl-col);width:100%;margin:0 auto;box-sizing:border-box}
#cl-composer .form{background:none!important;border:0!important;box-shadow:none!important;gap:var(--cl-s2)!important}
#cl-errwrap{padding:0!important;border:0!important;background:none!important;min-height:0!important}
.cl-err{display:flex;gap:10px;align-items:flex-start;background:var(--cl-danger-bg);border:1px solid var(--cl-danger-border);color:var(--cl-danger-text);
  border-radius:var(--cl-r-sm);padding:8px 10px;font-size:.88rem}
.cl-err span{flex:1}
.cl-err button{background:none;border:0;cursor:pointer;color:inherit;font-size:1.1rem;line-height:1;padding:2px 8px;min-height:32px;min-width:32px}
#cl-whorow{flex-wrap:nowrap!important;align-items:center!important;gap:6px!important}
#cl-whorow>.block{border:0!important;padding:0!important;background:none!important;min-width:0!important;box-shadow:none!important}
#cl-who{flex:1 1 auto;overflow:visible!important}
#cl-who .wrap{display:flex!important;flex-wrap:nowrap!important;gap:6px!important;align-items:center;overflow:visible}
#cl-who label{display:flex!important;align-items:center;gap:6px;background:none!important;border:0!important;box-shadow:none!important;padding:2px!important;
  min-height:44px;cursor:pointer;border-radius:999px;flex:none;width:auto!important}
#cl-who input{appearance:none;-webkit-appearance:none;width:36px!important;height:36px!important;border-radius:50%!important;border:0!important;margin:0!important;
  background-size:cover!important;background-position:center!important;background-color:var(--cl-surface-2)!important;opacity:.78;cursor:pointer;flex:none;transition:opacity .12s,box-shadow .12s}
#cl-who input:hover{opacity:1}
#cl-who input:checked{opacity:1;box-shadow:0 0 0 2px var(--cl-bg),0 0 0 4px var(--cl-focus)!important}
#cl-who input:focus-visible{box-shadow:0 0 0 2px var(--cl-bg),0 0 0 5px var(--cl-text)!important}
#cl-who span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
#cl-who input:checked + span{position:static;width:auto;height:auto;clip:auto;overflow:hidden;text-overflow:ellipsis;max-width:120px;font-weight:650;font-size:.9rem;padding:0 8px 0 2px;color:var(--cl-text)}
.cl-clairmark{margin-left:auto;display:flex;align-items:center;gap:6px;color:var(--cl-muted);font-size:.8rem;flex:none}
.cl-clairmark .cl-clairimg{width:28px;height:28px}
.cl-clairmark.busy .cl-clairimg{animation:clpulse 1.4s infinite}
#cl-whorow>.cl-markwrap{flex:0 0 auto!important;width:auto!important}
#cl-msg{border:0!important;background:none!important;padding:0!important}
#cl-msg label{border:0!important}
#cl-msg .input-container{display:flex;align-items:center;gap:6px;border:1px solid var(--cl-border-strong);border-radius:999px;background:var(--cl-bg);padding:4px 4px 4px 16px;box-sizing:border-box}
#cl-msg .input-container:focus-within{border-color:var(--cl-focus);box-shadow:0 0 0 1px var(--cl-focus)}
#cl-msg input{flex:1;min-width:0;border:0!important;outline:0!important;box-shadow:none!important;background:none!important;min-height:40px;font-size:1rem}
#cl-msg input::placeholder{color:var(--cl-muted)}
#cl-msg .submit-button{flex:none;position:static!important;width:40px!important;height:40px!important;border-radius:50%!important;border:0;background:var(--cl-accent-strong)!important;
  color:#fff!important;display:flex;align-items:center;justify-content:center;cursor:pointer;padding:9px!important;margin:0!important}
#cl-msg .submit-button:hover{background:var(--cl-accent-hover)!important}
#cl-msg .submit-button svg{width:100%;height:100%}
#cl-msg .submit-button svg path{fill:#fff;stroke:#fff}

/* iOS: fonte < 16px nos campos faz o Safari dar zoom ao focar */
/* (sem prefixo .gradio-container: o Gradio ja escopa o CSS customizado) */
@media (hover:none) and (pointer:coarse){
  input:not([type=radio]):not([type=checkbox]):not([type=range]),textarea,select{font-size:16px!important}
}
@media (max-width:640px),(max-height:480px){
  input:not([type=radio]):not([type=checkbox]):not([type=range]),textarea,select{font-size:16px!important}
}
/* menu "..." das sub-abas (Dialogue state / Analytics): alvos de toque maiores */
#cl-sub .overflow-menu>button{min-width:44px;min-height:44px;display:inline-flex;align-items:center;justify-content:center}
#cl-subanalytics .overflow-menu>button{min-width:44px;min-height:44px;display:inline-flex;align-items:center;justify-content:center}
#cl-sub .overflow-dropdown{z-index:40}
#cl-subanalytics .overflow-dropdown{z-index:40}
#cl-sub .overflow-dropdown button{min-height:44px;font-size:1rem;white-space:nowrap;text-align:left;padding:0 16px}
#cl-subanalytics .overflow-dropdown button{min-height:44px;font-size:1rem;white-space:nowrap;text-align:left;padding:0 16px}
/* viewport baixo (teclado virtual / paisagem): libera espaco para o chat */
@media (max-height:560px){
  #cl-brand .cl-brandrow{display:none}
  #cl-main>.tab-wrapper [role=tab]{min-height:40px}
  #cl-chat-head{min-height:36px}
  #cl-composer{padding-top:var(--cl-s2)!important}
  .cl-empty .cl-clairimg{width:48px;height:48px}
  .cl-empty{padding:var(--cl-s3)}
  #cl-chat-head>.block.cl-statuswrap{display:none!important}
  #cl-chat-head{justify-content:flex-end}
  #cl-chat-head button.cl-linkbtn{min-height:36px}
  #cl-chat-head button.cl-clear{min-height:44px}
  #cl-composer{gap:4px!important}
  .cl-err{max-height:54px;overflow-y:auto;padding:4px 8px;font-size:.84rem}
}
/* miniaturas de avatar por participante: alvo de toque de 44 px (imagem de 36 px) em celular */
@media (max-width:760px),(max-height:480px){
  .cl-pav .wrap{gap:0!important}
  .cl-pav label{min-width:44px;min-height:44px!important;align-items:center;justify-content:center;padding:0!important}
  .cl-pav input{width:36px!important;height:36px!important}
  #cl-who label{min-width:44px;justify-content:center}
}
/* paisagem em celular: quem fala e o campo na mesma linha */
@media (max-height:480px) and (min-width:600px){
  #cl-composer{flex-direction:row!important;flex-wrap:wrap!important;align-items:center!important;column-gap:var(--cl-s3)!important}
  #cl-composer>#cl-errwrap{flex:1 1 100%}
  #cl-composer>#cl-whorow{flex:0 0 auto!important;width:auto!important}
  #cl-composer>#cl-msg{flex:1 1 300px!important;min-width:260px}
}
#cl-chat-head>.block.cl-statuswrap{min-height:0!important}
.cl-statuswrap .html-container,.cl-statuswrap .prose{padding:0!important;min-height:0!important}
@media (max-width:640px){
  #cl-chat-head{flex-wrap:wrap!important;gap:0 var(--cl-s2)!important}
  #cl-chat-head>.block.cl-statuswrap{flex:1 1 100%!important}
  #cl-chat-head button.cl-linkbtn{min-height:36px}
  #cl-chat-head button.cl-clear{min-height:44px}
  #cl-avatar .wrap{gap:8px}
  #cl-avatar input{width:60px!important;height:60px!important}
  .cl-pav input{width:36px!important;height:36px!important}
  .cl-brandrow small{display:none}
  .cl-statusline .pull,.cl-statusline .sep{display:none}
  .cl-statusline{gap:2px 14px}
  .cl-bubble{max-width:calc(100% - 44px)}
  .cl-clairmark span{display:none}
  .cl-pullrow{flex-wrap:wrap!important}
  #cl-main>.tab-wrapper [role=tab]{padding:0 10px}
}
@media (max-width:420px){
  .cl-col,.cl-hist{padding-left:14px!important;padding-right:14px!important}
}

/* ===== Demo em duas colunas de largura igual ===== */
.cl-colwide{max-width:1180px}
.cl-twocol{display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--cl-s4)!important;align-items:start;flex-wrap:nowrap!important}
.cl-twocol>.cl-half{min-width:0!important;width:auto!important;flex:none!important;display:flex;flex-direction:column;gap:var(--cl-s3)!important}
.cl-half>.cl-card{margin-bottom:0!important}
/* desktop: Opcoes da demo a esquerda, Configuracao da Clair (com a barra Salvar) a direita; empilhado: Configuracao primeiro (ordem do DOM) */
.cl-twocol>.cl-half-opt{grid-column:1;grid-row:1}
.cl-twocol>.cl-half-cfg{grid-column:2;grid-row:1}
@media (max-width:760px){
  .cl-twocol{grid-template-columns:minmax(0,1fr)}
  .cl-twocol>.cl-half-opt,.cl-twocol>.cl-half-cfg{grid-column:1;grid-row:auto}
}
/* barra de salvar: fim da coluna esquerda; fundo opaco e z-index acima dos rotulos do Gradio (que usam z-index proprio, ex. 40) */
#cl-savebar{position:sticky!important;bottom:0;z-index:2000;isolation:isolate;background:var(--cl-bg)!important;opacity:1!important;
  border:1px solid var(--cl-border-strong)!important;border-radius:var(--cl-r-md)!important;box-shadow:0 -8px 18px rgba(0,0,0,.14)!important;
  padding:var(--cl-s3)!important;gap:var(--cl-s2)!important;margin-bottom:env(safe-area-inset-bottom)}
#cl-savebar>*{position:relative;z-index:1;background:transparent}
#cl-savebar .block,#cl-savebar .form{background:none!important;border:0!important;box-shadow:none!important;padding:0!important;min-width:0!important}
#cl-savebar .cl-btnrow{flex-wrap:wrap!important;gap:var(--cl-s2)!important}
#cl-savebar button{min-height:44px;white-space:nowrap}
#cl-savebar button.primary{background:var(--cl-accent-strong)!important;color:#fff!important;border:0!important;font-weight:650!important}
#cl-savebar button.primary:hover{background:var(--cl-accent-hover)!important}
.cl-state{display:flex;align-items:flex-start;gap:10px;font-size:.9rem;color:var(--cl-text-2)}
.cl-state .ic{flex:none;margin-top:1px}
.cl-state .txt{display:flex;flex-direction:column;gap:2px;min-width:0}
.cl-state b{font-size:.95rem;color:var(--cl-text)}
.cl-state .hint{font-size:.82rem;color:var(--cl-text-2);line-height:1.35}
.cl-state .hint.warn{color:var(--cl-muted)}
.cl-state.ok{color:var(--cl-ok)} .cl-state.ok b{color:var(--cl-ok)}
.cl-state.warn{color:var(--cl-accent-strong)} .cl-state.warn b{color:var(--cl-accent-strong)}
.cl-state.bad{color:var(--cl-danger-text)} .cl-state.bad b{color:var(--cl-danger-text)}
.cl-state.busy{color:var(--cl-muted)}
.cl-state .spin{transform-origin:12px 12px;animation:clspin 1s linear infinite}
@keyframes clspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion: reduce){.cl-state .spin{animation:none}}
@media (max-width:640px){
  #cl-savebar{padding:var(--cl-s2) var(--cl-s3)!important}
  /* barra compacta: estado em ate 2 linhas + 3 botoes numa fileira (rotulos podem quebrar linha) */
  #cl-savebar .cl-btnrow{flex-wrap:nowrap!important;gap:6px!important}
  #cl-savebar .cl-btnrow>button{flex:1 1 0!important;min-width:0!important;padding:0 6px!important;font-size:.85rem;white-space:normal!important;line-height:1.1;min-height:46px}
  #cl-savebar .cl-btnrow>button.primary{flex:1.3 1 0!important}
  #cl-savebar .cl-state .hint{font-size:.78rem;line-height:1.25;}
  #cl-savebar .cl-state{gap:8px}
  /* quem fala: 6 miniaturas com alvo de 44 px cabem em 360 px; o nome fica no placeholder do campo */
  #cl-who label{min-height:44px;min-width:44px;justify-content:center;padding:2px!important}
  #cl-who .wrap{gap:0!important}
  #cl-who input:checked + span{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);padding:0}
}
@media (max-height:560px){ #cl-savebar{position:static!important} }

/* ===== Revisao mobile (fase 3b) ===== */
html{scroll-padding-bottom:220px}
/* barra Salvar: grade 2 colunas (360 px), 4 colunas a partir de 390 px; rotulos quebram so entre palavras */
@media (max-width:640px){
  #cl-savebar .cl-btnrow{display:grid!important;grid-template-columns:1fr 1fr;gap:8px!important;flex-wrap:initial!important}
  #cl-savebar .cl-btnrow>button{min-width:0!important;width:100%!important;flex:none!important;padding:0 6px!important;word-break:normal!important;
    overflow-wrap:normal!important;hyphens:none!important;white-space:normal!important}
  #cl-savebar .cl-btnrow>button.primary{grid-column:1/-1}
  #cl-savebar .cl-state .hint{display:block}
}
@media (min-width:390px) and (max-width:640px){
  #cl-savebar .cl-btnrow{grid-template-columns:repeat(4,minmax(0,1fr))}
  #cl-savebar .cl-btnrow>button.primary{grid-column:auto}
}
/* contraste */
footer,footer a,footer button,footer .divider{color:var(--cl-text-2)!important;opacity:1!important}
footer a,footer button{min-height:44px;display:inline-flex;align-items:center;padding:0 8px}
#cl-avatar input:checked + span{color:var(--cl-text)}
.cl-clairimg{box-shadow:0 0 0 2px var(--cl-clair-tag),0 0 0 3px var(--cl-bg)}
/* alvos de toque */
#cl-msg .submit-button{width:44px!important;height:44px!important}
#cl-chat-head button.cl-linkbtn{min-height:44px!important}
#cl-lang [role=option],#cl-lang ul.options li{padding:12px 16px!important;min-height:44px}
#cl-grouprow{flex-wrap:wrap!important}
#cl-grouprow>.block:first-child{flex:1 1 160px!important;min-width:0!important}
#cl-lang .wrap{border:1px solid var(--cl-border-strong)!important;border-radius:var(--cl-r-md)!important;padding:0!important;height:44px!important;min-height:44px!important}
#cl-lang .wrap-inner{padding:0 6px!important;height:42px!important;min-height:42px!important}
/* acordeoes alinhados aos campos */
#cl-guide-acc,.cl-half .accordion,.cl-card .accordion{margin-inline:0!important;width:100%!important;max-width:100%!important;box-sizing:border-box}
/* simulacao (collabsim): indicador de digitacao, barra de observacao no lugar do composer, cartao da sub-aba */
.cl-typing{display:flex;align-items:center;gap:4px;color:var(--cl-muted);font-size:.85rem;padding:2px 0 2px 44px}
#cl-composer>#cl-simbar{flex:1 1 100%!important;align-items:center!important;flex-wrap:wrap!important;gap:var(--cl-s2)!important}
#cl-simbar>.block:first-child,#cl-simbar>.html-container:first-child{flex:1 1 220px!important;min-width:0!important}
#cl-simbar button{min-height:44px!important}
.cl-simstatus{display:flex;flex-direction:column;gap:2px;font-size:.9rem;color:var(--cl-text-2)}
.cl-simstatus b{color:var(--cl-text);font-weight:600}
.cl-simstatus .hint{color:var(--cl-text-2)}
.cl-simstatus .hint.warn{color:var(--cl-danger-text)}
#cl-simcard .cl-btnrow,#cl-simbar{flex-wrap:wrap!important}
"""


def build_css() -> str:
    uris = avatar_uris()
    vars_ = " ".join(f'--cl-av-{i + 1}:url("{u}");' for i, u in enumerate(uris))
    classes = "\n".join(f".cl-av{i + 1}{{background-image:var(--cl-av-{i + 1})}}" for i in range(N_AVATARS))
    radio = "\n".join(f"#cl-avatar label:nth-of-type({i + 1}) input{{background-image:var(--cl-av-{i + 1})!important}}"
                       for i in range(N_AVATARS))
    for p in range(N_PEOPLE):
        radio += "\n" + "\n".join(f"#cl-pav-{p + 1} label:nth-of-type({i + 1}) input{{background-image:var(--cl-av-{i + 1})!important}}"
                                   for i in range(N_AVATARS))
    return (_CSS_TEMPLATE.replace("@@AVVARS@@", vars_).replace("@@AVCLASSES@@", classes).replace("@@AVRADIO@@", radio))

#cl-test-btn{min-height:44px;min-width:44px}
