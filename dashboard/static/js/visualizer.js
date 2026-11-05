/**
 * visualizer.js — Next-Gen Web Dashboard 2.0
 * 100% Offline HTML5 Canvas Audio Waveform Spectrum Animation
 * Uses pure mathematics (Math.sin, harmonics) to animate spectrum bars
 * reacting dynamically to playback states (Playing, Paused, Idle).
 */

(function () {
  'use strict';

  class AudioSpectrumVisualizer {
    constructor(canvasId) {
      this.canvas = document.getElementById(canvasId);
      if (!this.canvas) return;

      this.ctx = this.canvas.getContext('2d');
      this.barCount = 36;
      this.peaks = new Array(this.barCount).fill(0);
      this.currentBars = new Array(this.barCount).fill(0);
      this.time = 0;
      this.isPlaying = false;
      this.isPaused = false;
      this.volume = 1.0;
      this.animId = null;

      this._initResize();
      this.start();
    }

    _initResize() {
      const resize = () => {
        const rect = this.canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = (rect.width || 360) * dpr;
        this.canvas.height = (rect.height || 64) * dpr;
        this.ctx.scale(dpr, dpr);
        this.width = rect.width || 360;
        this.height = rect.height || 64;
      };
      window.addEventListener('resize', resize);
      resize();
    }

    setPlaybackState(isPlaying, isPaused, volume = 1.0) {
      this.isPlaying = !!isPlaying;
      this.isPaused = !!isPaused;
      this.volume = Math.max(0.1, Math.min(2.0, volume));
    }

    start() {
      if (this.animId) cancelAnimationFrame(this.animId);
      const loop = () => {
        this.render();
        this.animId = requestAnimationFrame(loop);
      };
      this.animId = requestAnimationFrame(loop);
    }

    stop() {
      if (this.animId) {
        cancelAnimationFrame(this.animId);
        this.animId = null;
      }
    }

    render() {
      if (!this.ctx || !this.width) return;
      const ctx = this.ctx;
      const w = this.width;
      const h = this.height;

      ctx.clearRect(0, 0, w, h);

      this.time += this.isPlaying ? 0.08 : 0.02;

      const barWidth = (w - (this.barCount - 1) * 3) / this.barCount;
      const gradient = ctx.createLinearGradient(0, h, 0, 0);
      gradient.addColorStop(0, '#5865f2');
      gradient.addColorStop(0.5, '#7c5cbf');
      gradient.addColorStop(1, '#00b4d8');

      for (let i = 0; i < this.barCount; i++) {
        let targetHeight = 4; // Idle base

        if (this.isPlaying) {
          // Dynamic harmonic math synthesis
          const harmonic1 = Math.sin(this.time * 2.0 + i * 0.28);
          const harmonic2 = Math.cos(this.time * 1.3 - i * 0.42);
          const harmonic3 = Math.sin(this.time * 3.5 + i * 0.15);

          // Sub-bass frequency emphasis for lower bars
          const bassBoost = i < 8 ? Math.max(0, Math.sin(this.time * 2.5)) * 1.5 : 1.0;
          const rawMag = Math.abs(harmonic1 * 0.5 + harmonic2 * 0.35 + harmonic3 * 0.25) * bassBoost;

          targetHeight = Math.max(4, rawMag * (h * 0.85) * (this.volume / 1.0));
        } else if (this.isPaused) {
          // Soft breathing wave when paused
          targetHeight = Math.max(4, Math.sin(this.time + i * 0.2) * 12 + 10);
        }

        // Smooth damping
        this.currentBars[i] += (targetHeight - this.currentBars[i]) * 0.22;
        const barH = Math.min(h - 4, this.currentBars[i]);

        // Peak decay calculation
        if (barH > this.peaks[i]) {
          this.peaks[i] = barH;
        } else {
          this.peaks[i] = Math.max(0, this.peaks[i] - 1.2);
        }

        const x = i * (barWidth + 3);
        const y = h - barH;

        // Draw frequency bar
        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, barH, [3, 3, 0, 0]);
        ctx.fill();

        // Draw bouncing peak cap
        if (this.isPlaying && this.peaks[i] > 6) {
          ctx.fillStyle = '#38bdf8';
          ctx.fillRect(x, h - this.peaks[i] - 2, barWidth, 2);
        }
      }
    }
  }

  // Export to global scope
  window.AudioSpectrumVisualizer = AudioSpectrumVisualizer;
})();

