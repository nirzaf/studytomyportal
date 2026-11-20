/* Derived from MIT-licensed danielw-sudo/agentuto @ 697025a14aabbd391072fbedc522d1d68bebcbc4
 * Hardened for Studytomy Pilot 01 (F-03): tracks mid-run parameter mutations so
 * isSystemIsolated becomes false if body parameters change without a clean reset.
 */

import type { SimulationEngine, SimulationState, TelemetryPoint } from './types.ts';
import {
  clampMomentumParams,
  MOMENTUM_DEFAULT_PARAMS,
  type MomentumParams,
} from './MomentumConfig.ts';

export interface BodyDisk {
  x: number;
  y: number;
  vx: number;
  vy: number;
  mass: number;
  radius: number;
}

export interface MomentumState extends SimulationState {
  time: number;
  body1: BodyDisk;
  body2: BodyDisk;
  comX: number;
  comY: number;
  comVx: number;
  comVy: number;
  totalMomentumX: number;
  totalMomentumY: number;
  totalMomentumMag: number;
  initialTotalMomentumX: number;
  initialTotalMomentumY: number;
  momentumConservationError: number;
  kineticEnergy1: number;
  kineticEnergy2: number;
  totalKineticEnergy: number;
  initialKineticEnergy: number;
  comKineticEnergy: number;
  internalKineticEnergy: number;
  energyLostJoules: number;
  collisionCount: number;
  wallContactCount: number;
  parameterMutationCount: number;
  parameterMutatedMidRun: boolean;
  isSystemIsolated: boolean;
  restitution: number;
  justCollided: boolean;
}

const MIN_CLOSING_SPEED = 1e-12;
const MIN_SHALLOWNESS = 1e-10;
const MAX_EVENTS_PER_STEP = 4096;

export class MomentumEngine implements SimulationEngine<MomentumParams, MomentumState> {
  private params: MomentumParams = { ...MOMENTUM_DEFAULT_PARAMS };
  private time = 0;

  private b1: BodyDisk = { x: -3.5, y: 0, vx: 5.0, vy: 0, mass: 2.0, radius: 0.35 };
  private b2: BodyDisk = { x: 3.5, y: 0, vx: -3.0, vy: 0, mass: 2.0, radius: 0.35 };

  private initialP: { x: number; y: number } = { x: 0, y: 0 };
  private initialK = 0;
  private collisionCount = 0;
  private wallContactCount = 0;
  private parameterMutationCount = 0;
  private justCollided = false;

  private readonly boundX = 5.2;
  private readonly boundY = 2.8;

  init(params: MomentumParams): void {
    this.reset(params);
  }

  reset(params?: Partial<MomentumParams>): void {
    this.params = clampMomentumParams(params ? { ...this.params, ...params } : this.params);
    this.time = 0;
    this.collisionCount = 0;
    this.wallContactCount = 0;
    this.parameterMutationCount = 0;
    this.justCollided = false;

    const m1 = this.params.mass1;
    const m2 = this.params.mass2;
    const v1 = this.params.velocity1;
    const v2 = this.params.velocity2;
    const b = this.params.impactParameter;

    this.b1 = { x: -3.5, y: b * 0.5, vx: v1, vy: 0.0, mass: m1, radius: 0.35 };
    this.b2 = { x: 3.5, y: -b * 0.5, vx: v2, vy: 0.0, mass: m2, radius: 0.35 };

    this.initialP = { x: m1 * v1 + m2 * v2, y: 0.0 };
    this.initialK = 0.5 * m1 * v1 * v1 + 0.5 * m2 * v2 * v2;
  }

  dispose(): void {
    // Pure computation
  }

  step(dt: number): void {
    this.justCollided = false;
    let remaining = dt;

    for (let events = 0; events < MAX_EVENTS_PER_STEP && remaining > 0; events += 1) {
      const contact = this.timeToContact();
      const wall = this.nextWall();
      const next = Math.min(contact, wall.time);

      if (next >= remaining) {
        this.advance(remaining);
        break;
      }

      this.advance(next);
      remaining -= next;

      if (contact <= wall.time) {
        this.collide();
      } else if (wall.body) {
        this.bounce(wall.body, wall.axis);
      }
    }

    this.time += dt;
  }

  private advance(seconds: number): void {
    this.b1.x += this.b1.vx * seconds;
    this.b1.y += this.b1.vy * seconds;
    this.b2.x += this.b2.vx * seconds;
    this.b2.y += this.b2.vy * seconds;
  }

  private timeToContact(): number {
    const dx = this.b2.x - this.b1.x;
    const dy = this.b2.y - this.b1.y;
    const wx = this.b2.vx - this.b1.vx;
    const wy = this.b2.vy - this.b1.vy;
    const dw = dx * wx + dy * wy;
    if (-dw / Math.hypot(dx, dy) <= MIN_CLOSING_SPEED) return Infinity;

    const minDist = this.b1.radius + this.b2.radius;
    const gap = dx * dx + dy * dy - minDist * minDist;
    if (gap <= 0) return 0;

    const discriminant = dw * dw - (wx * wx + wy * wy) * gap;
    if (discriminant <= MIN_SHALLOWNESS * dw * dw) return Infinity;

    return gap / (Math.sqrt(discriminant) - dw);
  }

  private nextWall(): { time: number; body: BodyDisk | null; axis: 'x' | 'y' } {
    let first: { time: number; body: BodyDisk | null; axis: 'x' | 'y' } = {
      time: Infinity,
      body: null,
      axis: 'x',
    };
    for (const body of [this.b1, this.b2]) {
      const xLimit = this.boundX - body.radius;
      const yLimit = this.boundY - body.radius;
      const toX = body.vx === 0 ? Infinity : Math.max(0, (Math.sign(body.vx) * xLimit - body.x) / body.vx);
      const toY = body.vy === 0 ? Infinity : Math.max(0, (Math.sign(body.vy) * yLimit - body.y) / body.vy);
      if (toX < first.time) first = { time: toX, body, axis: 'x' };
      if (toY < first.time) first = { time: toY, body, axis: 'y' };
    }
    return first;
  }

  private collide(): void {
    const e = Math.max(0.0, Math.min(1.0, this.params.restitution));
    const contactX = this.b2.x - this.b1.x;
    const contactY = this.b2.y - this.b1.y;
    const contact = Math.hypot(contactX, contactY);
    const nx = contactX / contact;
    const ny = contactY / contact;

    const vrelN = (this.b1.vx - this.b2.vx) * nx + (this.b1.vy - this.b2.vy) * ny;
    const m1 = this.b1.mass;
    const m2 = this.b2.mass;
    const J = (-(1.0 + e) * vrelN) / (1.0 / m1 + 1.0 / m2);

    this.b1.vx += (J / m1) * nx;
    this.b1.vy += (J / m1) * ny;
    this.b2.vx -= (J / m2) * nx;
    this.b2.vy -= (J / m2) * ny;

    this.collisionCount++;
    this.justCollided = true;
  }

  private bounce(body: BodyDisk, axis: 'x' | 'y'): void {
    if (axis === 'x') {
      body.vx = -body.vx;
    } else {
      body.vy = -body.vy;
    }
    this.wallContactCount++;
  }

  getState(): MomentumState {
    const m1 = this.b1.mass;
    const m2 = this.b2.mass;
    const mTot = m1 + m2;

    const comX = (m1 * this.b1.x + m2 * this.b2.x) / mTot;
    const comY = (m1 * this.b1.y + m2 * this.b2.y) / mTot;
    const comVx = (m1 * this.b1.vx + m2 * this.b2.vx) / mTot;
    const comVy = (m1 * this.b1.vy + m2 * this.b2.vy) / mTot;

    const p1x = m1 * this.b1.vx;
    const p1y = m1 * this.b1.vy;
    const p2x = m2 * this.b2.vx;
    const p2y = m2 * this.b2.vy;
    const totalPx = p1x + p2x;
    const totalPy = p1y + p2y;
    const totalPmag = Math.hypot(totalPx, totalPy);

    const momentumConservationError = Math.hypot(
      totalPx - this.initialP.x,
      totalPy - this.initialP.y
    );

    const v1Sq = this.b1.vx * this.b1.vx + this.b1.vy * this.b1.vy;
    const v2Sq = this.b2.vx * this.b2.vx + this.b2.vy * this.b2.vy;
    const ke1 = 0.5 * m1 * v1Sq;
    const ke2 = 0.5 * m2 * v2Sq;
    const totalKe = ke1 + ke2;

    const vcmSq = comVx * comVx + comVy * comVy;
    const comKe = 0.5 * mTot * vcmSq;
    const internalKe = Math.max(0.0, totalKe - comKe);
    const energyLost = Math.max(0.0, this.initialK - totalKe);
    const parameterMutatedMidRun = this.parameterMutationCount > 0;

    return {
      time: this.time,
      body1: { ...this.b1 },
      body2: { ...this.b2 },
      comX,
      comY,
      comVx,
      comVy,
      totalMomentumX: totalPx,
      totalMomentumY: totalPy,
      totalMomentumMag: totalPmag,
      initialTotalMomentumX: this.initialP.x,
      initialTotalMomentumY: this.initialP.y,
      momentumConservationError,
      kineticEnergy1: ke1,
      kineticEnergy2: ke2,
      totalKineticEnergy: totalKe,
      initialKineticEnergy: this.initialK,
      comKineticEnergy: comKe,
      internalKineticEnergy: internalKe,
      energyLostJoules: energyLost,
      collisionCount: this.collisionCount,
      wallContactCount: this.wallContactCount,
      parameterMutationCount: this.parameterMutationCount,
      parameterMutatedMidRun,
      isSystemIsolated: this.wallContactCount === 0 && !parameterMutatedMidRun,
      restitution: this.params.restitution,
      justCollided: this.justCollided,
    };
  }

  /**
   * Updates parameters. Any change applied without reset() marks the run as
   * externally altered so isSystemIsolated cannot falsely remain true.
   */
  setParameters(params: Partial<MomentumParams>): void {
    const next = clampMomentumParams({ ...this.params, ...params });
    const changed = (Object.keys(next) as (keyof MomentumParams)[]).some(
      (key) => next[key] !== this.params[key]
    );
    this.params = next;
    if (changed) {
      this.parameterMutationCount++;
    }
    if (params.mass1 !== undefined) this.b1.mass = next.mass1;
    if (params.mass2 !== undefined) this.b2.mass = next.mass2;
  }

  getParameters(): MomentumParams {
    return { ...this.params };
  }

  getTelemetrySnapshot(): TelemetryPoint {
    const s = this.getState();
    return {
      t: s.time,
      body1_x: s.body1.x,
      body1_y: s.body1.y,
      body1_vx: s.body1.vx,
      body1_vy: s.body1.vy,
      body2_x: s.body2.x,
      body2_y: s.body2.y,
      body2_vx: s.body2.vx,
      body2_vy: s.body2.vy,
      com_x: s.comX,
      com_y: s.comY,
      com_vx: s.comVx,
      com_vy: s.comVy,
      total_p_x: s.totalMomentumX,
      total_p_y: s.totalMomentumY,
      total_p_mag: s.totalMomentumMag,
      kinetic_energy_total_J: s.totalKineticEnergy,
      kinetic_energy_com_J: s.comKineticEnergy,
      kinetic_energy_internal_J: s.internalKineticEnergy,
      collision_count: s.collisionCount,
      wall_contacts: s.wallContactCount,
      is_isolated: s.isSystemIsolated ? 1 : 0,
    };
  }
}
