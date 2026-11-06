/**
 * app.js — Next-Gen Web Dashboard 2.0
 * Core Dashboard Application Controller
 * Handles WebSocket lifecycle, playback controls, Queue DnD,
 * Web Search with Direct Enqueue, and Synced Karaoke Lyrics with Click-to-Seek.
 */

(function () {
  'use strict';

  class DashboardApp {
    constructor() {
      this.apiSecret = window.DASHBOARD_CONFIG ? window.DASHBOARD_CONFIG.apiSecret : '';
      this.currentGuildId = null;
      this.currentState = null;
      this.ws = null;
      this.reconnectTimer = null;
      this.progressInterval = null;
      this.elapsedSeconds = 0;
      this.totalDuration = 0;
      this.isPaused = true;
      this.currentLyrics = [];

      this.visualizer = null;
      this.equalizer = null;

      this._initDOM();
      this._bindEvents();
      this._initSubsystems();
      this.connectWebSocket();
    }

    _initDOM() {
      this.guildSelect = document.getElementById('guild-select');
      this.statusDot = document.getElementById('status-dot');
      this.wsStatusText = document.getElementById('ws-status-text');

      // Player DOM
      this.trackArt = document.getElementById('track-art');
      this.artPlaceholder = document.getElementById('art-placeholder');
      this.trackTitle = document.getElementById('track-title');
      this.trackAuthor = document.getElementById('track-author');
      this.trackRequester = document.getElementById('track-requester');

      this.scrubberBar = document.getElementById('scrubber-bar');
      this.scrubberProgress = document.getElementById('scrubber-progress');
      this.timeElapsed = document.getElementById('time-elapsed');
      this.timeRemaining = document.getElementById('time-remaining');

      this.btnPlayPause = document.getElementById('btn-play-pause');
      this.btnSkip = document.getElementById('btn-skip');
      this.btnRewind = document.getElementById('btn-rewind');
      this.btnForward = document.getElementById('btn-forward');
      this.btnLoop = document.getElementById('btn-loop');
      this.volumeSlider = document.getElementById('volume-slider');
      this.volumeText = document.getElementById('volume-text');

      // Search DOM
      this.navSearchInput = document.getElementById('search-input') || document.getElementById('nav-search-input');
      this.btnNavSearchGo = document.getElementById('btn-nav-search-go');
      this.navSearchResults = document.getElementById('nav-search-results');

      // Queue DOM
      this.queueList = document.getElementById('queue-list');
      this.queueCount = document.getElementById('queue-count');
      this.queueTotalDur = document.getElementById('queue-total-dur');
      this.queueFilterInput = document.getElementById('queue-filter-input');
      this.btnShuffle = document.getElementById('btn-shuffle');
      this.btnClear = document.getElementById('btn-clear');

      // Lyrics Teleprompter DOM
      this.lyricsBox = document.getElementById('lyrics-teleprompter-box');
      this.lyricsTitle = document.getElementById('lyrics-track-title');

      // Toast DOM
      this.toastContainer = document.getElementById('toast-container');
    }

    _initSubsystems() {
      if (window.AudioSpectrumVisualizer) {
        this.visualizer = new window.AudioSpectrumVisualizer('audio-spectrum-canvas');
      }
      if (window.EqualizerController) {
        this.equalizer = new window.EqualizerController();
      }
    }

    _bindEvents() {
      // Theme Palette Switcher
      const btnThemeToggle = document.getElementById('btn-theme-toggle');
      if (btnThemeToggle) {
        const savedTheme = localStorage.getItem('dashboard_theme') || 'violet';
        document.body.setAttribute('data-theme', savedTheme);
        btnThemeToggle.setAttribute('data-theme', savedTheme);

        const themes = ['violet', 'midnight', 'emerald', 'cyberpunk'];
        btnThemeToggle.addEventListener('click', () => {
          const current = document.body.getAttribute('data-theme') || 'violet';
          const next = themes[(themes.indexOf(current) + 1) % themes.length];
          document.body.setAttribute('data-theme', next);
          btnThemeToggle.setAttribute('data-theme', next);
          localStorage.setItem('dashboard_theme', next);
          this.showToast(`🎨 Palette: ${next.toUpperCase()}`, 'info');
        });
      }

      // Guild selection
      if (this.guildSelect) {
        this.guildSelect.addEventListener('change', (e) => {
          this.selectGuild(parseInt(e.target.value, 10));
        });
      }

      // Playback Controls
      if (this.btnPlayPause) {
        this.btnPlayPause.addEventListener('click', () => this.handlePlayPause());
      }
      if (this.btnSkip) {
        this.btnSkip.addEventListener('click', () => this.handleSkip());
      }
      if (this.btnRewind) {
        this.btnRewind.addEventListener('click', () => this.seekDelta(-15));
      }
      if (this.btnForward) {
        this.btnForward.addEventListener('click', () => this.seekDelta(+15));
      }
      if (this.btnLoop) {
        this.btnLoop.addEventListener('click', () => this.cycleLoopMode());
      }

      // Volume
      if (this.volumeSlider) {
        this.volumeSlider.addEventListener('input', (e) => {
          const val = parseInt(e.target.value, 10);
          if (this.volumeText) this.volumeText.textContent = `${val}%`;
          this.setVolumeDebounced(val);
        });
      }

      // Scrubber Click
      if (this.scrubberBar) {
        this.scrubberBar.addEventListener('click', (e) => {
          if (!this.currentGuildId || this.totalDuration <= 0) return;
          const rect = this.scrubberBar.getBoundingClientRect();
          const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
          const targetSec = Math.floor(frac * this.totalDuration);
          this.seekTo(targetSec);
        });
      }

      // Web Search Bar
      if (this.btnNavSearchGo) {
        this.btnNavSearchGo.addEventListener('click', () => this.executeSearch());
      }
      if (this.navSearchInput) {
        this.navSearchInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') this.executeSearch();
          if (e.key === 'Escape') this.hideSearchResults();
        });
      }

      // Close dropdown when clicked outside
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.nav-search-bar') && this.navSearchResults) {
          this.hideSearchResults();
        }
      });

      // Search Results Click (Add to Queue / Play Next)
      if (this.navSearchResults) {
        this.navSearchResults.addEventListener('click', (e) => {
          const btn = e.target.closest('.btn-mini-add, .btn-mini-next');
          if (!btn) return;
          const url = btn.getAttribute('data-url');
          const isNext = btn.getAttribute('data-action') === 'next';
          if (url && this.currentGuildId) {
            this.directEnqueue(url, isNext);
          }
        });
      }

      // Queue Actions
      if (this.btnShuffle) {
        this.btnShuffle.addEventListener('click', () => this.shuffleQueue());
      }
      if (this.btnClear) {
        this.btnClear.addEventListener('click', () => this.clearQueue());
      }

      // Queue Filter
      if (this.queueFilterInput) {
        this.queueFilterInput.addEventListener('input', () => this.filterQueue());
      }

      // Teleprompter Click-to-Seek
      if (this.lyricsBox) {
        this.lyricsBox.addEventListener('click', (e) => {
          const line = e.target.closest('.lyric-line');
          if (!line) return;
          const startSec = parseFloat(line.getAttribute('data-start'));
          if (!isNaN(startSec)) {
            this.seekTo(Math.floor(startSec));
            this.showToast(`Jumped to ${line.querySelector('.lyric-time') ? line.querySelector('.lyric-time').textContent : startSec + 's'}`, 'info');
          }
        });
      }

      // Setup Queue Drag and Drop
      this._initQueueDragAndDrop();
    }

    /* ── WebSocket Management ────────────────────────────────────────────── */
    connectWebSocket() {
      const loc = window.location;
      const port = (window.DASHBOARD_CONFIG && window.DASHBOARD_CONFIG.port) || 8080;
      const host = loc.host || `127.0.0.1:${port}`;
      const proto = loc.protocol === 'https:' ? 'wss:' : 'ws:';
      let wsUrl = `${proto}//${host}/ws/dashboard`;
      if (this.apiSecret) {
        wsUrl += `?token=${encodeURIComponent(this.apiSecret)}`;
      }

      if (this.ws) {
        try { this.ws.close(); } catch (_) { }
      }

      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this._setWSStatus('online', 'Connected');
        if (this.currentGuildId) {
          this.sendWS('select_guild', { guild_id: this.currentGuildId });
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this._handleWSMessage(msg);
        } catch (err) {
          console.debug('WS parse error:', err);
        }
      };

      this.ws.onclose = () => {
        this._setWSStatus('offline', 'Disconnected');
        if (!this.reconnectTimer) {
          this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.connectWebSocket();
          }, 3000);
        }
      };

      this.ws.onerror = () => {
        this._setWSStatus('offline', 'Error');
      };
    }

    sendWS(action, data = {}) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ action, guild_id: this.currentGuildId, ...data }));
        return true;
      }
      return false;
    }

    _setWSStatus(state, label) {
      if (this.statusDot) {
        this.statusDot.className = `status-dot ${state}`;
      }
      if (this.wsStatusText) {
        this.wsStatusText.textContent = label;
      }
    }

    _handleWSMessage(msg) {
      if (msg.type === 'init') {
        if (msg.guilds) this._renderGuildSelect(msg.guilds);
        if (msg.selected_guild) this.applyGuildState(msg.selected_guild);
      } else if (msg.type === 'guild_state') {
        if (msg.data) this.applyGuildState(msg.data);
      } else if (msg.type === 'lyrics') {
        if (msg.data) this.renderLyrics(msg.data);
      } else if (msg.type === 'action_result') {
        if (msg.result && !msg.result.success && msg.result.error) {
          this.showToast(msg.result.error, 'error');
        }
      }
    }

    /* ── REST Fallback Helper ────────────────────────────────────────────── */
    apiFetch(endpoint, options = {}) {
      options.headers = options.headers || {};
      if (this.apiSecret) {
        options.headers['Authorization'] = `Bearer ${this.apiSecret}`;
      }
      if (window.location.protocol === 'file:') {
        const port = (window.DASHBOARD_CONFIG && window.DASHBOARD_CONFIG.port) || 8080;
        endpoint = `http://127.0.0.1:${port}${endpoint}`;
      }
      return fetch(endpoint, options);
    }

    /* ── State Synchronization ───────────────────────────────────────────── */
    _renderGuildSelect(guilds) {
      if (!this.guildSelect) return;
      this.guildSelect.innerHTML = guilds
        .map((g) => `<option value="${g.id}">${g.name} (${g.queue_size} tracks)</option>`)
        .join('');

      if (!this.currentGuildId && guilds.length > 0) {
        this.selectGuild(parseInt(guilds[0].id, 10));
      }
    }

    selectGuild(guildId) {
      this.currentGuildId = guildId;
      if (this.guildSelect) this.guildSelect.value = String(guildId);
      this.sendWS('select_guild', { guild_id: guildId });
      if (this.equalizer) this.equalizer.setGuild(guildId);
      this.fetchStateViaREST(guildId);
      this.fetchLyrics(guildId);
    }

    async fetchStateViaREST(guildId) {
      try {
        const res = await this.apiFetch(`/api/v1/guild/${guildId}/state`);
        if (res.ok) {
          const state = await res.json();
          this.applyGuildState(state);
        }
      } catch (e) {
        console.debug('Failed to fetch state via REST', e);
      }
    }

    applyGuildState(state) {
      this.currentState = state;
      const isPlaying = !!state.is_playing;
      this.isPaused = !!state.is_paused;

      // Update Visualizer
      if (this.visualizer) {
        this.visualizer.setPlaybackState(isPlaying, this.isPaused, state.volume || 1.0);
      }

      // Update Equalizer & Effects
      if (this.equalizer) {
        this.equalizer.updateState(state);
      }

      // Update Play/Pause Button
      if (this.btnPlayPause) {
        this.btnPlayPause.innerHTML = isPlaying
          ? '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"></rect><rect x="14" y="4" width="4" height="16"></rect></svg>'
          : '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>';
      }

      // Update Volume
      if (state.volume !== undefined && this.volumeSlider) {
        this.volumeSlider.value = state.volume_percent || Math.round(state.volume * 100);
        if (this.volumeText) this.volumeText.textContent = `${this.volumeSlider.value}%`;
      }

      // Update Track Info
      const np = state.now_playing;
      if (np) {
        if (this.trackTitle) this.trackTitle.textContent = np.title || 'Unknown Title';
        if (this.trackAuthor) this.trackAuthor.textContent = np.uploader || '—';
        if (this.trackRequester) this.trackRequester.textContent = np.requested_by ? `Queued by ${np.requested_by}` : 'Added from Web Dashboard';

        if (this.trackArt) {
          if (np.thumbnail) {
            this.trackArt.src = np.thumbnail;
            this.trackArt.style.display = 'block';
            if (this.artPlaceholder) this.artPlaceholder.style.display = 'none';
          } else {
            this.trackArt.style.display = 'none';
            if (this.artPlaceholder) this.artPlaceholder.style.display = 'flex';
          }
        }

        this.elapsedSeconds = np.elapsed || 0;
        this.totalDuration = np.duration || 0;
        this._updateScrubberUI();
      } else {
        if (this.trackTitle) this.trackTitle.textContent = 'Playback Idle';
        if (this.trackAuthor) this.trackAuthor.textContent = '—';
        if (this.trackRequester) this.trackRequester.textContent = 'Use search bar above to queue tracks';
        if (this.trackArt) this.trackArt.style.display = 'none';
        if (this.artPlaceholder) this.artPlaceholder.style.display = 'flex';

        this.elapsedSeconds = 0;
        this.totalDuration = 0;
        this._updateScrubberUI();
      }

      // Update Queue
      this.renderQueue(state.queue || []);
      if (this.queueCount) this.queueCount.textContent = `${state.queue_size || 0} track(s)`;
      if (this.queueTotalDur) this.queueTotalDur.textContent = `Total: ${this._fmtDuration(state.total_duration || 0)}`;

      // Client Progress Timer Loop
      this._startProgressTimer(isPlaying);
    }

    _startProgressTimer(isPlaying) {
      if (this.progressInterval) clearInterval(this.progressInterval);
      if (!isPlaying || this.isPaused || this.totalDuration <= 0) return;

      this.progressInterval = setInterval(() => {
        if (this.elapsedSeconds < this.totalDuration) {
          this.elapsedSeconds += 1;
          this._updateScrubberUI();
          this._syncTeleprompter();
        }
      }, 1000);
    }

    _updateScrubberUI() {
      if (this.timeElapsed) this.timeElapsed.textContent = this._fmtDuration(this.elapsedSeconds);
      if (this.timeRemaining) {
        const rem = Math.max(0, this.totalDuration - this.elapsedSeconds);
        this.timeRemaining.textContent = `-${this._fmtDuration(rem)}`;
      }
      if (this.scrubberProgress && this.totalDuration > 0) {
        const pct = Math.min(100, (this.elapsedSeconds / this.totalDuration) * 100);
        this.scrubberProgress.style.width = `${pct}%`;
      }
    }

    /* ── Web Search & Direct Enqueue ──────────────────────────────────────── */
    async executeSearch() {
      if (!this.navSearchInput) return;
      const query = (this.navSearchInput.value || '').trim();
      if (!query) return;

      if (this.btnNavSearchGo) this.btnNavSearchGo.textContent = 'Searching…';

      try {
        const res = await this.apiFetch(`/api/v1/dashboard/search?q=${encodeURIComponent(query)}&limit=5`);
        const data = await res.json();
        if (this.btnNavSearchGo) this.btnNavSearchGo.textContent = 'Search';

        if (data && data.results && data.results.length > 0) {
          this._renderSearchResults(data.results);
        } else {
          this.showToast('No tracks found', 'info');
          this.hideSearchResults();
        }
      } catch (err) {
        if (this.btnNavSearchGo) this.btnNavSearchGo.textContent = 'Search';
        this.showToast('Search failed', 'error');
      }
    }

    _renderSearchResults(results) {
      if (!this.navSearchResults) return;
      this.navSearchResults.innerHTML = results
        .map(
          (t) => `
        <div class="search-result-item">
          <img class="search-result-thumb" src="${t.thumbnail || ''}" onerror="this.style.display='none'" />
          <div class="search-result-info">
            <div class="search-result-title" title="${t.title}">${t.title}</div>
            <div class="search-result-sub">${t.uploader || 'Unknown'} • ${this._fmtDuration(t.duration)}</div>
          </div>
          <div class="search-result-btns">
            <button class="btn-mini-add" data-url="${t.url}" data-action="queue">➕ Queue</button>
            <button class="btn-mini-next" data-url="${t.url}" data-action="next">⏭️ Next</button>
          </div>
        </div>`
        )
        .join('');
      this.navSearchResults.style.display = 'block';
    }

    hideSearchResults() {
      if (this.navSearchResults) this.navSearchResults.style.display = 'none';
    }

    async directEnqueue(url, playNext = false) {
      if (!this.currentGuildId) return;
      this.hideSearchResults();
      this.showToast(playNext ? 'Adding as next track…' : 'Adding to queue…', 'info');

      const payload = { query: url, play_next: playNext };
      let success = false;

      if (!this.sendWS('add_to_queue', payload)) {
        try {
          const res = await this.apiFetch(`/api/v1/guild/${this.currentGuildId}/queue/add`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const d = await res.json();
          success = d.success;
        } catch (e) {
          success = false;
        }
      } else {
        success = true;
      }

      if (success) {
        this.showToast(playNext ? 'Playing next track!' : 'Enqueued track directly to MySQL!', 'success');
        if (this.navSearchInput) this.navSearchInput.value = '';
      }
    }

    /* ── Real-Time Synced Karaoke Lyrics Teleprompter ─────────────────────── */
    async fetchLyrics(guildId) {
      if (!guildId) return;
      try {
        const res = await this.apiFetch(`/api/v1/guild/${guildId}/lyrics`);
        if (res.ok) {
          const data = await res.json();
          this.renderLyrics(data);
        }
      } catch (_) { }
    }

    renderLyrics(data) {
      if (!this.lyricsBox) return;
      if (!data || !data.lines || data.lines.length === 0) {
        this.lyricsBox.innerHTML = '<div style="color:var(--text-dim); margin-top:2rem">No synced lyrics available for this track.</div>';
        this.currentLyrics = [];
        return;
      }

      this.currentLyrics = data.lines;
      if (this.lyricsTitle) this.lyricsTitle.textContent = data.title || '—';

      this.lyricsBox.innerHTML = data.lines
        .map(
          (line, idx) => `
        <div class="lyric-line" data-start="${line.start}" data-index="${idx}">
          <span class="lyric-time">${line.timestamp || ''}</span>${line.text}
        </div>`
        )
        .join('');

      this._syncTeleprompter();
    }

    _syncTeleprompter() {
      if (!this.lyricsBox || !this.currentLyrics || this.currentLyrics.length === 0) return;

      const lines = this.lyricsBox.querySelectorAll('.lyric-line');
      let activeIndex = -1;

      for (let i = 0; i < this.currentLyrics.length; i++) {
        const curStart = this.currentLyrics[i].start;
        const nextStart = i + 1 < this.currentLyrics.length ? this.currentLyrics[i + 1].start : Infinity;
        if (this.elapsedSeconds >= curStart && this.elapsedSeconds < nextStart) {
          activeIndex = i;
          break;
        }
      }

      lines.forEach((lineEl, idx) => {
        if (idx === activeIndex) {
          if (!lineEl.classList.contains('active')) {
            lineEl.classList.add('active');
            lineEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        } else {
          lineEl.classList.remove('active');
        }
      });
    }

    /* ── Controls Handlers ────────────────────────────────────────────────── */
    handlePlayPause() {
      if (!this.currentGuildId) return;
      const action = this.isPaused ? 'resume' : 'pause';
      if (!this.sendWS('play_pause')) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/${action}`, { method: 'POST' });
      }
    }

    handleSkip() {
      if (!this.currentGuildId) return;
      if (!this.sendWS('skip')) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/skip`, { method: 'POST' });
      }
      this.showToast('Skipping current track…', 'info');
    }

    seekDelta(delta) {
      if (!this.currentGuildId || this.totalDuration <= 0) return;
      const target = Math.max(0, Math.min(this.totalDuration, this.elapsedSeconds + delta));
      this.seekTo(target);
    }

    seekTo(seconds) {
      if (!this.currentGuildId) return;
      this.elapsedSeconds = seconds;
      this._updateScrubberUI();
      this._syncTeleprompter();

      if (!this.sendWS('seek', { position: seconds })) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/seek`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ position: seconds }),
        });
      }
    }

    setVolumeDebounced(vol) {
      if (this._volDebounce) clearTimeout(this._volDebounce);
      this._volDebounce = setTimeout(() => {
        if (!this.currentGuildId) return;
        if (!this.sendWS('volume', { volume: vol })) {
          this.apiFetch(`/api/v1/guild/${this.currentGuildId}/volume`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ volume: vol }),
          });
        }
      }, 150);
    }

    cycleLoopMode() {
      if (!this.currentGuildId || !this.currentState) return;
      const cur = this.currentState.loop_mode || 'off';
      const modes = ['off', 'track', 'queue'];
      const next = modes[(modes.indexOf(cur) + 1) % modes.length];
      if (!this.sendWS('loop', { mode: next })) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/loop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: next }),
        });
      }
      this.showToast(`Loop mode: ${next.toUpperCase()}`, 'info');
    }

    shuffleQueue() {
      if (!this.currentGuildId) return;
      if (!this.sendWS('shuffle_queue')) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/queue/shuffle`, { method: 'POST' });
      }
      this.showToast('Queue shuffled!', 'success');
    }

    clearQueue() {
      if (!this.currentGuildId) return;
      if (!this.sendWS('clear_queue')) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/queue/clear`, { method: 'POST' });
      }
      this.showToast('Queue cleared', 'info');
    }

    /* ── Queue Drag-and-Drop ──────────────────────────────────────────────── */
    renderQueue(queue) {
      if (!this.queueList) return;
      if (queue.length === 0) {
        this.queueList.innerHTML = '<li style="text-align:center; padding:2rem; color:var(--text-dim)">Queue is empty.</li>';
        return;
      }

      this.queueList.innerHTML = queue
        .map(
          (t, idx) => `
        <li class="queue-item" draggable="true" data-index="${idx}">
          <span class="item-drag-handle">☰</span>
          <span class="item-index">${idx + 1}</span>
          <img class="item-thumb" src="${t.thumbnail || ''}" onerror="this.style.display='none'" />
          <div class="item-info">
            <div class="item-title" title="${t.title}">${t.title}</div>
            <div class="item-sub">${t.uploader || 'Unknown'} • Added by ${t.requested_by || 'User'}</div>
          </div>
          <span class="item-dur">${this._fmtDuration(t.duration)}</span>
          <button class="item-del" data-index="${idx}" title="Remove track">✕</button>
        </li>`
        )
        .join('');

      // Delete buttons
      this.queueList.querySelectorAll('.item-del').forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const idx = parseInt(btn.getAttribute('data-index'), 10);
          this.removeQueueItem(idx);
        });
      });
    }

    _initQueueDragAndDrop() {
      if (!this.queueList) return;
      let dragSrcIdx = null;

      this.queueList.addEventListener('dragstart', (e) => {
        const item = e.target.closest('.queue-item');
        if (!item) return;
        dragSrcIdx = parseInt(item.getAttribute('data-index'), 10);
        item.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });

      this.queueList.addEventListener('dragover', (e) => {
        e.preventDefault();
        const item = e.target.closest('.queue-item');
        if (!item) return;
        this.queueList.querySelectorAll('.queue-item').forEach((el) => el.classList.remove('drag-over'));
        item.classList.add('drag-over');
      });

      this.queueList.addEventListener('drop', (e) => {
        e.preventDefault();
        const item = e.target.closest('.queue-item');
        if (!item || dragSrcIdx === null) return;
        const toIdx = parseInt(item.getAttribute('data-index'), 10);
        if (dragSrcIdx !== toIdx) {
          this.moveQueueItem(dragSrcIdx, toIdx);
        }
      });

      this.queueList.addEventListener('dragend', () => {
        this.queueList.querySelectorAll('.queue-item').forEach((el) => {
          el.classList.remove('dragging');
          el.classList.remove('drag-over');
        });
        dragSrcIdx = null;
      });
    }

    moveQueueItem(fromIdx, toIdx) {
      if (!this.currentGuildId) return;
      if (!this.sendWS('move_queue', { from_index: fromIdx, to_index: toIdx })) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/queue/move`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ from_index: fromIdx, to_index: toIdx }),
        });
      }
    }

    removeQueueItem(index) {
      if (!this.currentGuildId) return;
      if (!this.sendWS('remove_queue', { index })) {
        this.apiFetch(`/api/v1/guild/${this.currentGuildId}/queue/${index}`, { method: 'DELETE' });
      }
      this.showToast(`Removed track #${index + 1}`, 'info');
    }

    filterQueue() {
      if (!this.queueFilterInput || !this.queueList) return;
      const term = this.queueFilterInput.value.toLowerCase().trim();
      this.queueList.querySelectorAll('.queue-item').forEach((item) => {
        const text = item.textContent.toLowerCase();
        item.style.display = text.includes(term) ? 'flex' : 'none';
      });
    }

    /* ── Toast Helper ─────────────────────────────────────────────────────── */
    showToast(message, type = 'info') {
      if (!this.toastContainer) return;
      const t = document.createElement('div');
      t.className = `toast ${type}`;
      t.textContent = message;
      this.toastContainer.appendChild(t);
      setTimeout(() => {
        t.style.opacity = '0';
        setTimeout(() => t.remove(), 250);
      }, 3000);
    }

    _fmtDuration(sec) {
      const s = Math.floor(sec || 0);
      const m = Math.floor(s / 60);
      const remSec = s % 60;
      return `${m}:${remSec < 10 ? '0' : ''}${remSec}`;
    }
  }

  // Launch when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    window.dashboardApp = new DashboardApp();
  });
})();

