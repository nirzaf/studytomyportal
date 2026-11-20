/* Derived from MIT-licensed danielw-sudo/agentuto @ 697025a14aabbd391072fbedc522d1d68bebcbc4 */

export interface SimulationState {
  time: number;
  [key: string]: unknown;
}

export interface TelemetryPoint {
  t: number;
  [key: string]: number;
}

export interface ParameterDescriptor<T = number> {
  id: string;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  defaultValue: T;
  description?: string;
}

export interface SimulationEngine<
  TParams = Record<string, number>,
  TState extends SimulationState = SimulationState,
> {
  init(params: TParams): void;
  reset(params?: TParams): void;
  dispose(): void;
  step(dt: number): void;
  getState(): TState;
  setParameters(params: Partial<TParams>): void;
  getParameters(): TParams;
  getTelemetrySnapshot(): TelemetryPoint;
}

export interface ViewportRenderer<TState extends SimulationState = SimulationState> {
  mount(container: HTMLElement | HTMLCanvasElement): void;
  render(state: TState, interpolationAlpha?: number): void;
  resize(width: number, height: number): void;
  unmount(): void;
}
