(function () {
  'use strict';

  // Resolve backend URL: prefer explicit data-backend attribute, then script src origin.
  // data-backend is needed when the theme loads scripts deferred (document.currentScript is null).
  const _script = document.currentScript
    || Array.from(document.scripts).find(s => s.src && s.src.includes('/static/widget.js'));
  const BACKEND = (_script && _script.getAttribute('data-backend'))
    || (_script ? new URL(_script.src).origin : window.location.origin);

  // ── Inject CSS ────────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #ai-chat-root {
      position: fixed; bottom: 24px; right: 24px; z-index: 99999;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    #ai-chat-toggle {
      width: 56px; height: 56px; border-radius: 50%;
      background: #111; color: #fff; border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 16px rgba(0,0,0,0.25);
      transition: transform 0.2s, box-shadow 0.2s; margin-left: auto;
    }
    #ai-chat-toggle:hover { transform: scale(1.08); box-shadow: 0 6px 20px rgba(0,0,0,0.3); }
    #ai-chat-window {
      position: absolute; bottom: 70px; right: 0;
      width: 360px; height: 520px; background: #fff; border-radius: 16px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.18);
      display: none; flex-direction: column; overflow: hidden;
      animation: ai-slide-up 0.2s ease;
    }
    #ai-chat-window.open { display: flex; }
    @keyframes ai-slide-up {
      from { opacity: 0; transform: translateY(12px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    #ai-chat-header {
      background: #111; color: #fff; padding: 14px 16px;
      display: flex; align-items: center;
    }
    #ai-chat-header-info { display: flex; align-items: center; gap: 10px; }
    #ai-chat-avatar {
      width: 36px; height: 36px; background: rgba(255,255,255,0.15);
      border-radius: 50%; display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 700;
    }
    #ai-chat-title  { font-weight: 600; font-size: 14px; }
    #ai-chat-status { font-size: 11px; opacity: 0.65; margin-top: 2px; }
    #ai-chat-messages {
      flex: 1; overflow-y: auto; padding: 16px;
      display: flex; flex-direction: column; gap: 10px; scroll-behavior: smooth;
    }
    .ai-msg {
      max-width: 82%; padding: 10px 14px; border-radius: 16px;
      font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-break: break-word;
    }
    .ai-msg a { color: inherit; text-decoration: underline; }
    .ai-msg--user {
      background: #111; color: #fff; align-self: flex-end; border-bottom-right-radius: 4px;
    }
    .ai-msg--assistant {
      background: #f4f4f4; color: #111; align-self: flex-start; border-bottom-left-radius: 4px;
    }
    #ai-chat-typing { padding: 0 16px 10px; display: flex; gap: 4px; align-items: center; }
    #ai-chat-typing span {
      width: 7px; height: 7px; background: #bbb; border-radius: 50%;
      animation: ai-bounce 1.2s infinite;
    }
    #ai-chat-typing span:nth-child(2) { animation-delay: 0.2s; }
    #ai-chat-typing span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes ai-bounce {
      0%, 80%, 100% { transform: translateY(0); }
      40% { transform: translateY(-5px); }
    }
    #ai-chat-input-area {
      display: flex; align-items: center; gap: 8px; padding: 12px; border-top: 1px solid #eee;
    }
    #ai-chat-input {
      flex: 1; padding: 9px 14px; border: 1.5px solid #e0e0e0; border-radius: 24px;
      font-size: 14px; outline: none; transition: border-color 0.15s;
    }
    #ai-chat-input:focus { border-color: #111; }
    #ai-chat-send {
      width: 38px; height: 38px; border-radius: 50%;
      background: #111; color: #fff; border: none; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; transition: background 0.15s;
    }
    #ai-chat-send:hover    { background: #333; }
    #ai-chat-send:disabled { background: #ccc; cursor: default; }
    .ai-order-confirmed {
      background: #f0fdf4; border: 1.5px solid #86efac; border-radius: 12px;
      padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; width: 100%;
    }
    .ai-order-confirmed__icon  { font-size: 20px; }
    .ai-order-confirmed__title { font-weight: 700; font-size: 14px; color: #15803d; }
    .ai-order-confirmed__number { font-size: 13px; color: #166534; }
    .ai-order-confirmed__note  { font-size: 11px; color: #16a34a; margin-top: 2px; }
    .ai-defect-reported {
      background: #fffbeb; border: 1.5px solid #fcd34d; border-radius: 12px;
      padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; width: 100%;
    }
    .ai-defect-reported__icon  { font-size: 20px; }
    .ai-defect-reported__title { font-weight: 700; font-size: 14px; color: #92400e; }
    .ai-defect-reported__note  { font-size: 12px; color: #b45309; margin-top: 2px; }
    .ai-product-grid {
      display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; width: 100%; margin-top: 4px;
    }
    .ai-product-card {
      background: #fff; border: 1.5px solid #e8e8e8; border-radius: 12px;
      overflow: hidden; text-decoration: none; color: #111;
      transition: box-shadow 0.15s, transform 0.15s; display: flex; flex-direction: column;
    }
    .ai-product-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.12); transform: translateY(-2px); }
    .ai-product-card__img  { width: 100%; aspect-ratio: 1; object-fit: cover; background: #f4f4f4; display: block; }
    .ai-product-card__img.loading { min-height: 120px; }
    .ai-product-card__info { padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 2px; flex: 1; }
    .ai-product-card__title {
      font-size: 12px; font-weight: 600; line-height: 1.3; overflow: hidden;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    }
    .ai-product-card__price { font-size: 12px; color: #555; margin-top: 2px; }
    .ai-product-card__cta {
      margin-top: 6px; font-size: 11px; font-weight: 600; color: #fff;
      background: #111; border-radius: 6px; padding: 4px 8px; text-align: center;
    }
  `;
  document.head.appendChild(style);

  // ── Inject HTML ───────────────────────────────────────────────────────────────
  const root = document.createElement('div');
  root.id = 'ai-chat-root';
  root.innerHTML = `
    <button id="ai-chat-toggle" aria-label="Open shopping assistant">
      <span id="ai-chat-icon-open">
        <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
      </span>
      <span id="ai-chat-icon-close" style="display:none">
        <svg width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </span>
    </button>
    <div id="ai-chat-window" aria-live="polite">
      <div id="ai-chat-header">
        <div id="ai-chat-header-info">
          <div id="ai-chat-avatar">AI</div>
          <div>
            <div id="ai-chat-title">CakeCart Assistant</div>
            <div id="ai-chat-status">Online</div>
          </div>
        </div>
      </div>
      <div id="ai-chat-messages">
        <div class="ai-msg ai-msg--assistant">
          👋 Hi! I can help you find products, check prices, and place your order. What are you looking for?
        </div>
      </div>
      <div id="ai-chat-typing" style="display:none">
        <span></span><span></span><span></span>
      </div>
      <div id="ai-chat-input-area">
        <input type="text" id="ai-chat-input" placeholder="Search products, ask questions…" autocomplete="off" />
        <button id="ai-chat-send" aria-label="Send message">
          <svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <line x1="22" y1="2" x2="11" y2="13"/>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
        </button>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  // ── App ───────────────────────────────────────────────────────────────────────
  let sessionId = null;
  let isOpen    = false;
  let isBusy    = false;

  const toggle    = document.getElementById('ai-chat-toggle');
  const window_   = document.getElementById('ai-chat-window');
  const messages  = document.getElementById('ai-chat-messages');
  const input     = document.getElementById('ai-chat-input');
  const sendBtn   = document.getElementById('ai-chat-send');
  const typing    = document.getElementById('ai-chat-typing');
  const iconOpen  = document.getElementById('ai-chat-icon-open');
  const iconClose = document.getElementById('ai-chat-icon-close');

  async function ensureSession() {
    if (sessionId) return;
    try {
      const res  = await fetch(`${BACKEND}/api/sessions`, { method: 'POST' });
      const data = await res.json();
      sessionId  = data.session_id;
    } catch {
      appendMessage('assistant', '⚠️ Could not connect to the assistant. Please try again later.');
    }
  }

  toggle.addEventListener('click', async () => {
    isOpen = !isOpen;
    window_.classList.toggle('open', isOpen);
    iconOpen.style.display  = isOpen ? 'none' : '';
    iconClose.style.display = isOpen ? ''     : 'none';
    if (isOpen) { await ensureSession(); input.focus(); }
  });

  // Render text with markdown links ([label](url)) as real <a> elements.
  // All text nodes use createTextNode — no innerHTML for agent/user content.
  function renderText(el, text) {
    while (el.firstChild) el.removeChild(el.firstChild);
    const linkRe = /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g;
    let last = 0, m;
    while ((m = linkRe.exec(text)) !== null) {
      if (m.index > last) el.appendChild(document.createTextNode(text.slice(last, m.index)));
      const a = document.createElement('a');
      a.href = m[2]; a.target = '_blank'; a.rel = 'noopener';
      a.textContent = m[1];
      el.appendChild(a);
      last = linkRe.lastIndex;
    }
    if (last < text.length) el.appendChild(document.createTextNode(text.slice(last)));
  }

  function appendMessage(role, text) {
    const div = document.createElement('div');
    div.className = `ai-msg ai-msg--${role}`;
    renderText(div, text);
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function appendOrderConfirmed(orderNumber) {
    const card = document.createElement('div');
    card.className = 'ai-order-confirmed';

    const icon = document.createElement('div');
    icon.className = 'ai-order-confirmed__icon';
    icon.textContent = '✅';

    const title = document.createElement('div');
    title.className = 'ai-order-confirmed__title';
    title.textContent = 'Order confirmed!';

    const num = document.createElement('div');
    num.className = 'ai-order-confirmed__number';
    num.textContent = `Order #${orderNumber}`;

    const note = document.createElement('div');
    note.className = 'ai-order-confirmed__note';
    note.textContent = 'Payment collected on delivery — no card needed.';

    card.append(icon, title, num, note);
    messages.appendChild(card);
    messages.scrollTop = messages.scrollHeight;
  }

  function appendDefectReported(orderNumber) {
    const card = document.createElement('div');
    card.className = 'ai-defect-reported';

    const icon = document.createElement('div');
    icon.className = 'ai-defect-reported__icon';
    icon.textContent = '📋';

    const title = document.createElement('div');
    title.className = 'ai-defect-reported__title';
    title.textContent = 'Report submitted';

    const note = document.createElement('div');
    note.className = 'ai-defect-reported__note';
    note.textContent = (orderNumber ? `Order ${orderNumber} — ` : '') + 'The store team has been notified and will follow up within 24 hours.';

    card.append(icon, title, note);
    messages.appendChild(card);
    messages.scrollTop = messages.scrollHeight;
  }

  function appendProductCards(products) {
    if (!products || !products.length) return;
    const grid = document.createElement('div');
    grid.className = 'ai-product-grid';

    products.forEach(p => {
      const a = document.createElement('a');
      a.className = 'ai-product-card';
      a.href = p.url || '#';
      a.target = '_blank'; a.rel = 'noopener';

      const img = document.createElement('img');
      img.className = 'ai-product-card__img loading';
      img.alt = p.title || '';
      img.onload  = () => img.classList.remove('loading');
      img.onerror = () => { img.style.display = 'none'; };
      if (p.image_url) img.src = p.image_url;

      const info = document.createElement('div');
      info.className = 'ai-product-card__info';

      const titleEl = document.createElement('div');
      titleEl.className = 'ai-product-card__title';
      titleEl.textContent = p.title || '';

      const priceEl = document.createElement('div');
      priceEl.className = 'ai-product-card__price';
      priceEl.textContent = p.price || '';

      const cta = document.createElement('div');
      cta.className = 'ai-product-card__cta';
      cta.textContent = 'View product →';

      info.append(titleEl, priceEl, cta);
      a.append(img, info);
      grid.appendChild(a);
    });

    messages.appendChild(grid);
    messages.scrollTop = messages.scrollHeight;
  }

  async function sendMessage() {
    const text = input.value.trim();
    if (!text || isBusy || !sessionId) return;

    isBusy = true;
    input.value = '';
    sendBtn.disabled = true;
    appendMessage('user', text);

    typing.style.display = 'flex';
    messages.scrollTop   = messages.scrollHeight;

    const bubble = appendMessage('assistant', '');
    let   buffer = '';

    try {
      const res = await fetch(`${BACKEND}/api/sessions/${sessionId}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let   raw     = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        raw += decoder.decode(value, { stream: true });
        const lines = raw.split('\n');
        raw = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.type === 'text') {
            typing.style.display = 'none';
            buffer += event.content;
            renderText(bubble, buffer);
            messages.scrollTop = messages.scrollHeight;
          } else if (event.type === 'products') {
            typing.style.display = 'none';
            appendProductCards(event.products);
          } else if (event.type === 'order_confirmed') {
            typing.style.display = 'none';
            appendOrderConfirmed(event.order_number);
          } else if (event.type === 'defect_reported') {
            typing.style.display = 'none';
            appendDefectReported(event.order_number);
          } else if (event.type === 'tool') {
            typing.style.display = 'flex';
          } else if (event.type === 'done') {
            typing.style.display = 'none';
          } else if (event.type === 'error') {
            typing.style.display = 'none';
            bubble.textContent = '⚠️ ' + (event.message || 'Something went wrong. Please try again.');
          }
        }
      }
    } catch {
      typing.style.display = 'none';
      bubble.textContent = '⚠️ Connection lost. Please try again.';
    }

    isBusy = false;
    sendBtn.disabled = false;
    input.focus();
  }

  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
})();
