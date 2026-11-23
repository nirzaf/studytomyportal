/* Derived from MIT-licensed danielw-sudo/agentuto @ 697025a14aabbd391072fbedc522d1d68bebcbc4 */

import type { ViewportRenderer } from './types.ts';
import type { MomentumState } from './MomentumEngine.ts';

export class MomentumRenderer2D implements ViewportRenderer<MomentumState> {
  private canvas: HTMLCanvasElement | null = null;
  private ctx: CanvasRenderingContext2D | null = null;
  private width = 800;
  private height = 460;
  private comTrail: { x: number; y: number }[] = [];

  mount(container: HTMLElement | HTMLCanvasElement): void {
    if (container instanceof HTMLCanvasElement) {
      this.canvas = container;
    } else {
      this.canvas = document.createElement('canvas');
      this.canvas.style.width = '100%';
      this.canvas.style.height = '100%';
      this.canvas.style.display = 'block';
      container.appendChild(this.canvas);
    }

    this.ctx = this.canvas.getContext('2d');
    this.resize(container.clientWidth || 800, container.clientHeight || 460);
  }

  clearTrail(): void {
    this.comTrail = [];
  }

  resize(width: number, height: number): void {
    this.width = Math.max(280, width);
    this.height = Math.max(220, height);
    if (!this.canvas) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = this.width * dpr;
    this.canvas.height = this.height * dpr;
    if (this.ctx) {
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  render(state: MomentumState): void {
    if (!this.ctx || !this.canvas) return;
    const ctx = this.ctx;
    const w = this.width;
    const h = this.height;

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#060913';
    ctx.fillRect(0, 0, w, h);

    const centerX = w * 0.5;
    const centerY = h * 0.54;
    const scale = Math.min(w / 12.0, h / 7.2);

    const toScreenX = (x: number) => centerX + x * scale;
    const toScreenY = (y: number) => centerY - y * scale;

    const tableW = 10.4 * scale;
    const tableH = 5.6 * scale;
    const tableLeft = centerX - tableW * 0.5;
    const tableTop = centerY - tableH * 0.5;

    ctx.fillStyle = '#0a0f1d';
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(tableLeft, tableTop, tableW, tableH, 12);
    ctx.fill();
    ctx.stroke();

    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = -5; x <= 5; x++) {
      const sx = toScreenX(x);
      ctx.moveTo(sx, tableTop);
      ctx.lineTo(sx, tableTop + tableH);
    }
    for (let y = -2; y <= 2; y++) {
      const sy = toScreenY(y);
      ctx.moveTo(tableLeft, sy);
      ctx.lineTo(tableLeft + tableW, sy);
    }
    ctx.stroke();

    if (state.time === 0) {
      this.comTrail = [];
    }
    this.comTrail.push({ x: state.comX, y: state.comY });
    if (this.comTrail.length > 60) this.comTrail.shift();

    ctx.strokeStyle = 'rgba(16, 185, 129, 0.45)';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    for (let i = 0; i < this.comTrail.length; i++) {
      const p = this.comTrail[i];
      const sx = toScreenX(p.x);
      const sy = toScreenY(p.y);
      if (i === 0) ctx.moveTo(sx, sy);
      else ctx.lineTo(sx, sy);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    const b1x = toScreenX(state.body1.x);
    const b1y = toScreenY(state.body1.y);
    const b2x = toScreenX(state.body2.x);
    const b2y = toScreenY(state.body2.y);

    const comSx = toScreenX(state.comX);
    const comSy = toScreenY(state.comY);
    ctx.save();
    ctx.beginPath();
    ctx.arc(comSx, comSy, 7, 0, Math.PI * 2);
    ctx.strokeStyle = state.isSystemIsolated ? '#10b981' : '#f59e0b';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.fillStyle = state.isSystemIsolated ? '#34d399' : '#fbbf24';
    ctx.font = 'bold 10px monospace';
    ctx.textAlign = 'center';
    ctx.fillText(
      state.isSystemIsolated
        ? 'CoM (V=const)'
        : state.parameterMutatedMidRun
          ? 'CoM (params edited)'
          : 'CoM (wall impulse)',
      comSx,
      comSy - 12
    );
    ctx.restore();

    const drawPuck = (
      px: number,
      py: number,
      vx: number,
      vy: number,
      mass: number,
      radius: number,
      fill: string,
      stroke: string,
      arrow: string,
      label: string
    ) => {
      const rPx = radius * scale;
      ctx.save();
      ctx.beginPath();
      ctx.arc(px, py, rPx, 0, Math.PI * 2);
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2.5;
      ctx.stroke();

      const len = Math.hypot(vx, vy) * scale * 0.18;
      if (len > 2) {
        const angle = Math.atan2(-vy, vx);
        ctx.strokeStyle = arrow;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(px + Math.cos(angle) * len, py + Math.sin(angle) * len);
        ctx.stroke();
      }

      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(`${label} (${mass.toFixed(1)}kg)`, px, py + 3);
      ctx.restore();
    };

    drawPuck(
      b1x, b1y, state.body1.vx, state.body1.vy, state.body1.mass, state.body1.radius,
      '#0284c7', '#38bdf8', '#7dd3fc', 'm1'
    );
    drawPuck(
      b2x, b2y, state.body2.vx, state.body2.vy, state.body2.mass, state.body2.radius,
      '#d97706', '#f59e0b', '#fde68a', 'm2'
    );

    if (state.justCollided) {
      ctx.beginPath();
      ctx.arc((b1x + b2x) * 0.5, (b1y + b2y) * 0.5, 24, 0, Math.PI * 2);
      ctx.strokeStyle = '#fde047';
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    ctx.fillStyle = 'rgba(15, 23, 42, 0.9)';
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.roundRect(12, 12, Math.min(285, w - 24), 110, 8);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = state.isSystemIsolated ? '#10b981' : '#f59e0b';
    ctx.font = 'bold 10px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(
      state.isSystemIsolated
        ? 'ISOLATED SYSTEM (F_ext = 0)'
        : state.parameterMutatedMidRun
          ? 'PARAMETER EDITED MID-RUN (RESET REQUIRED)'
          : 'WALL CONTACT: EXTERNAL IMPULSE APPLIED',
      22,
      30
    );

    ctx.fillStyle = '#e2e8f0';
    ctx.font = '11px monospace';
    ctx.fillText(`P_total: [${state.totalMomentumX.toFixed(2)}, ${state.totalMomentumY.toFixed(2)}] kg·m/s`, 22, 50);
    ctx.fillText(`V_cm:    [${state.comVx.toFixed(2)}, ${state.comVy.toFixed(2)}] m/s`, 22, 68);
    ctx.fillText(`KE:      ${state.totalKineticEnergy.toFixed(2)} J (lost ${state.energyLostJoules.toFixed(2)} J)`, 22, 86);
    ctx.fillText(`Hits: ${state.collisionCount} | Walls: ${state.wallContactCount} | e=${state.restitution.toFixed(2)}`, 22, 104);
  }

  unmount(): void {
    this.canvas = null;
    this.ctx = null;
    this.comTrail = [];
  }
}
