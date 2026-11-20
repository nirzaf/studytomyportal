/* Derived from MIT-licensed danielw-sudo/agentuto @ 697025a14aabbd391072fbedc522d1d68bebcbc4 */

import type { ParameterDescriptor } from './types.ts';

export interface MomentumParams extends Record<string, number> {
  mass1: number;
  mass2: number;
  velocity1: number;
  velocity2: number;
  impactParameter: number;
  restitution: number;
}

export const MOMENTUM_DEFAULT_PARAMS: MomentumParams = {
  mass1: 2.0,
  mass2: 2.0,
  velocity1: 5.0,
  velocity2: -3.0,
  impactParameter: 0.0,
  restitution: 1.0,
};

export const MOMENTUM_METADATA = {
  id: 'conservation-momentum',
  title: 'Conservation of Linear Momentum & Collisions',
  discipline: 'Physics',
  curriculum: 'Sri Lankan National School Syllabus (NIE / MoE) — Grade 12 A/L Physics Mechanics (Draft Pilot 01)',
  sourceCommit: '697025a14aabbd391072fbedc522d1d68bebcbc4',
  formattedODE: 'dP_total / dt = 0,  V_cm = (m1*v1 + m2*v2) / (m1 + m2) = const',
  description:
    'Explore closed-system linear momentum conservation, center-of-mass velocity invariance, elastic vs inelastic collisions (restitution e = 0 to 1), and external wall impulses using SI units.',
} as const;

export const MOMENTUM_TELEMETRY_UNITS: Record<string, string> = {
  t: 's',
  body1_x: 'm',
  body1_y: 'm',
  body1_vx: 'm/s',
  body1_vy: 'm/s',
  body2_x: 'm',
  body2_y: 'm',
  body2_vx: 'm/s',
  body2_vy: 'm/s',
  com_x: 'm',
  com_y: 'm',
  com_vx: 'm/s',
  com_vy: 'm/s',
  total_p_x: 'kg*m/s',
  total_p_y: 'kg*m/s',
  total_p_mag: 'kg*m/s',
  kinetic_energy_total_J: 'J',
  kinetic_energy_com_J: 'J',
  kinetic_energy_internal_J: 'J',
  collision_count: 'count',
  wall_contacts: 'count',
  is_isolated: 'flag',
};

export const MOMENTUM_PARAM_DESCRIPTORS: ParameterDescriptor<number>[] = [
  {
    id: 'mass1',
    label: 'Body 1 Mass (m1)',
    min: 0.5,
    max: 10.0,
    step: 0.5,
    defaultValue: 2.0,
    unit: 'kg',
    description: 'Inertial mass of body 1.',
  },
  {
    id: 'mass2',
    label: 'Body 2 Mass (m2)',
    min: 0.5,
    max: 10.0,
    step: 0.5,
    defaultValue: 2.0,
    unit: 'kg',
    description: 'Inertial mass of body 2.',
  },
  {
    id: 'velocity1',
    label: 'Body 1 Initial Velocity (v1)',
    min: -10.0,
    max: 10.0,
    step: 0.5,
    defaultValue: 5.0,
    unit: 'm/s',
    description: 'Initial horizontal velocity of body 1.',
  },
  {
    id: 'velocity2',
    label: 'Body 2 Initial Velocity (v2)',
    min: -10.0,
    max: 10.0,
    step: 0.5,
    defaultValue: -3.0,
    unit: 'm/s',
    description: 'Initial horizontal velocity of body 2.',
  },
  {
    id: 'impactParameter',
    label: 'Impact Offset (b)',
    min: -1.2,
    max: 1.2,
    step: 0.1,
    defaultValue: 0.0,
    unit: 'm',
    description: 'Vertical distance between trajectories (0 = head-on 1D collision).',
  },
  {
    id: 'restitution',
    label: 'Restitution (e)',
    min: 0.0,
    max: 1.0,
    step: 0.05,
    defaultValue: 1.0,
    unit: 'coeff',
    description: 'e = 1.0 elastic; e = 0.0 perfectly inelastic along the collision axis.',
  },
];

export function clampMomentumParams(input: Partial<MomentumParams>): MomentumParams {
  const clamp = (val: number | undefined, fallback: number, min: number, max: number) => {
    const n = typeof val === 'number' && Number.isFinite(val) ? val : fallback;
    return Math.min(max, Math.max(min, n));
  };
  return {
    mass1: clamp(input.mass1, MOMENTUM_DEFAULT_PARAMS.mass1, 0.5, 10.0),
    mass2: clamp(input.mass2, MOMENTUM_DEFAULT_PARAMS.mass2, 0.5, 10.0),
    velocity1: clamp(input.velocity1, MOMENTUM_DEFAULT_PARAMS.velocity1, -10.0, 10.0),
    velocity2: clamp(input.velocity2, MOMENTUM_DEFAULT_PARAMS.velocity2, -10.0, 10.0),
    impactParameter: clamp(input.impactParameter, MOMENTUM_DEFAULT_PARAMS.impactParameter, -1.2, 1.2),
    restitution: clamp(input.restitution, MOMENTUM_DEFAULT_PARAMS.restitution, 0.0, 1.0),
  };
}
