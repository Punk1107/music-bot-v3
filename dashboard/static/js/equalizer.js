/**
 * equalizer.js — Next-Gen Web Dashboard 2.0
 * Interactive 4-Band Graphic EQ & 18 Audio Effects Toggle Board with Glowing LED
 * Presets persistence to MySQL server_configs.guild_presets.
 */

(function () {
  'use strict';

  class EqualizerController {
    constructor() {
      this.currentGuildId = null;
      this.eqDebounce = null;
      this.eqBands = { sub_bass: 0.0, bass: 0.0, mid: 0.0, treble: 0.0 };
      this.activePreset = 'flat';

      this._initElements();
      this._bindEvents();
    }

    _initElements() {
      this.sliderSubBass = document.getElementById('eq-sub-bass');
      this.sliderBass = document.getElementById('eq-bass');
      this.sliderMid = document.getElementById('eq-mid');
      this.sliderTreble = document.getElementById('eq-treble');

      this.valSubBass = document.getElementById('eq-sub-bass-val');
      this.valBass = document.getElementById('eq-bass-val');
      this.valMid = document.getElementById('eq-mid-val');
      this.valTreble = document.getElementById('eq-treble-val');

      this.presetBtns = document.querySelectorAll('.btn-preset');
      this.effectsGrid = document.getElementById('effects-board-grid');

      this.presetInput = document.getElementById('custom-preset-input');
      this.btnSavePreset = document.getElementById('btn-save-preset');
      this.presetSelect = document.getElementById('custom-preset-select');
      this.btnLoadPreset = document.getElementById('btn-load-preset');
      this.btnDelPreset = document.getElementById('btn-del-preset');
    }

    _bindEvents() {
      const sliders = [
        { el: this.sliderSubBass, key: 'sub_bass', valEl: this.valSubBass },
        { el: this.sliderBass, key: 'bass', valEl: this.valBass },
        { el: this.sliderMid, key: 'mid', valEl: this.valMid },
        { el: this.sliderTreble, key: 'treble', valEl: this.valTreble },
      ];

      sliders.forEach(({ el, key, valEl }) => {
        if (!el) return;
        el.addEventListener('input', (e) => {
          const val = parseFloat(e.target.value);
          this.eqBands[key] = val;
          if (valEl) valEl.textContent = (val > 0 ? '+' : '') + val.toFixed(1) + ' dB';
          this.activePreset = 'custom';
          this._highlightActivePreset('custom');
          this._dispatchEqualizerDebounced();
        });
      });

      // Studio Presets
      if (this.presetBtns) {
        this.presetBtns.forEach((btn) => {
          btn.addEventListener('click', () => {
            const p = btn.getAttribute('data-preset');
            if (p) this.applyPreset(p);
          });
        });
      }

      // 18 Effects Cards Click
      if (this.effectsGrid) {
        this.effectsGrid.addEventListener('click', (e) => {
          const card = e.target.closest('.effect-card');
          if (!card) return;
          const effectId = card.getAttribute('data-effect');
          if (effectId && this.currentGuildId) {
            this.toggleEffect(effectId);
          }
        });
      }

      // MySQL Preset Save
      if (this.btnSavePreset) {
        this.btnSavePreset.addEventListener('click', () => {
          const name = (this.presetInput.value || '').trim();
          if (!name) {
            window.dashboardApp.showToast('Please enter a preset name', 'error');
            return;
          }
          this.savePresetToMySQL(name);
        });
      }

      // MySQL Preset Load
      if (this.btnLoadPreset) {
        this.btnLoadPreset.addEventListener('click', () => {
          const name = this.presetSelect.value;
          if (name) this.loadPresetFromMySQL(name);
        });
      }

      // MySQL Preset Delete
      if (this.btnDelPreset) {
        this.btnDelPreset.addEventListener('click', () => {
          const name = this.presetSelect.value;
          if (name) this.deletePresetFromMySQL(name);
        });
      }
    }

    setGuild(guildId) {
      this.currentGuildId = guildId;
      if (guildId) this.fetchPresetsList();
    }

    updateState(state) {
      if (!state) return;

      // 1. Update Equalizer Bands
      if (state.equalizer_bands) {
        this.eqBands = { ...this.eqBands, ...state.equalizer_bands };
        if (this.sliderSubBass) this.sliderSubBass.value = this.eqBands.sub_bass || 0;
        if (this.sliderBass) this.sliderBass.value = this.eqBands.bass || 0;
        if (this.sliderMid) this.sliderMid.value = this.eqBands.mid || 0;
        if (this.sliderTreble) this.sliderTreble.value = this.eqBands.treble || 0;

        if (this.valSubBass) this.valSubBass.textContent = this._fmtDb(this.eqBands.sub_bass);
        if (this.valBass) this.valBass.textContent = this._fmtDb(this.eqBands.bass);
        if (this.valMid) this.valMid.textContent = this._fmtDb(this.eqBands.mid);
        if (this.valTreble) this.valTreble.textContent = this._fmtDb(this.eqBands.treble);
      }

      if (state.equalizer_preset) {
        this.activePreset = state.equalizer_preset;
        this._highlightActivePreset(this.activePreset);
      }

      // 2. Update 18 Audio Effects Cards & LED Status
      if (state.all_effects && this.effectsGrid) {
        this._renderEffectsBoard(state.all_effects);
      } else if (state.effects && this.effectsGrid) {
        // Fallback if all_effects not present
        const activeSet = new Set(state.effects);
        document.querySelectorAll('.effect-card').forEach((card) => {
          const id = card.getAttribute('data-effect');
          if (activeSet.has(id)) {
            card.classList.add('active');
          } else {
            card.classList.remove('active');
          }
        });
      }

      // 3. Update Presets Dropdown
      if (state.presets && this.presetSelect) {
        this._renderPresetsDropdown(state.presets);
      }
    }

    _fmtDb(val) {
      const v = parseFloat(val) || 0;
      return (v > 0 ? '+' : '') + v.toFixed(1) + ' dB';
    }

    _highlightActivePreset(preset) {
      if (!this.presetBtns) return;
      this.presetBtns.forEach((btn) => {
        if (btn.getAttribute('data-preset') === preset) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });
    }

    _renderEffectsBoard(effects) {
      if (!this.effectsGrid) return;
      this.effectsGrid.innerHTML = effects
        .map(
          (eff) => `
        <div class="effect-card ${eff.enabled ? 'active' : ''}" data-effect="${eff.id}" title="${eff.name}">
          <div class="effect-card-top">
            <span class="effect-icon">${eff.name.split(' ')[0] || '🎵'}</span>
            <div class="led-indicator"></div>
          </div>
          <span class="effect-name">${eff.name.replace(/^[^\s]+\s*/, '')}</span>
        </div>`
        )
        .join('');
    }

    _renderPresetsDropdown(presets) {
      if (!this.presetSelect) return;
      const cur = this.presetSelect.value;
      this.presetSelect.innerHTML = presets
        .map((p) => `<option value="${p}">${p.charAt(0).toUpperCase() + p.slice(1)}</option>`)
        .join('');
      if (cur) this.presetSelect.value = cur;
    }

    _dispatchEqualizerDebounced() {
      if (this.eqDebounce) clearTimeout(this.eqDebounce);
      this.eqDebounce = setTimeout(() => {
        if (!this.currentGuildId) return;
        const payload = { bands: this.eqBands };
        if (!window.dashboardApp.sendWS('set_equalizer', payload)) {
          window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/equalizer`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
        }
      }, 150);
    }

    applyPreset(presetName) {
      if (!this.currentGuildId) return;
      this.activePreset = presetName;
      this._highlightActivePreset(presetName);

      const payload = { preset: presetName };
      if (!window.dashboardApp.sendWS('set_equalizer', payload)) {
        window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/equalizer`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }
      window.dashboardApp.showToast(`Applied preset: ${presetName}`, 'info');
    }

    toggleEffect(effectId) {
      if (!this.currentGuildId) return;
      // Optimistic LED toggle
      const card = document.querySelector(`.effect-card[data-effect="${effectId}"]`);
      if (card) card.classList.toggle('active');

      const payload = { effect: effectId };
      if (!window.dashboardApp.sendWS('toggle_effect', payload)) {
        window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/effects`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }
    }

    async fetchPresetsList() {
      if (!this.currentGuildId) return;
      try {
        const res = await window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/presets`);
        const data = await res.json();
        if (data && data.presets) {
          const names = Object.keys(data.presets);
          this._renderPresetsDropdown(names);
        }
      } catch (e) {
        console.debug('Failed to fetch presets', e);
      }
    }

    async savePresetToMySQL(name) {
      if (!this.currentGuildId) return;
      const payload = { name };
      try {
        let ok = false;
        if (!window.dashboardApp.sendWS('save_preset', payload)) {
          const res = await window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/presets/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          ok = data.success;
        } else {
          ok = true;
        }
        if (ok) {
          window.dashboardApp.showToast(`Preset "${name}" saved to MySQL!`, 'success');
          this.presetInput.value = '';
          this.fetchPresetsList();
        }
      } catch (err) {
        window.dashboardApp.showToast('Failed to save preset', 'error');
      }
    }

    async loadPresetFromMySQL(name) {
      if (!this.currentGuildId) return;
      const payload = { name };
      if (!window.dashboardApp.sendWS('load_preset', payload)) {
        await window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/presets/load`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }
      window.dashboardApp.showToast(`Loaded preset "${name}"`, 'success');
    }

    async deletePresetFromMySQL(name) {
      if (!this.currentGuildId) return;
      try {
        let ok = false;
        if (!window.dashboardApp.sendWS('delete_preset', { name })) {
          const res = await window.dashboardApp.apiFetch(`/api/v1/guild/${this.currentGuildId}/presets/${encodeURIComponent(name)}`, {
            method: 'DELETE',
          });
          const data = await res.json();
          ok = data.success;
        } else {
          ok = true;
        }
        if (ok) {
          window.dashboardApp.showToast(`Preset "${name}" deleted from MySQL`, 'info');
          this.fetchPresetsList();
        }
      } catch (e) {
        window.dashboardApp.showToast('Failed to delete preset', 'error');
      }
    }
  }

  // Export
  window.EqualizerController = EqualizerController;
})();

