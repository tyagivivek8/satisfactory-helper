export type VerificationStatus = "verified" | "needs_confirmation" | "blocked";
export type AgentProvider = "codex" | "claude";

export interface ProviderStatus {
  ready: boolean;
  auth: string;
  detail: string;
  label: string;
  model: string;
  models: Array<{ id: string; label: string }>;
}

export interface StatusResponse {
  state: "ready" | "degraded" | "blocked";
  generation: number;
  codex: { ready: boolean; auth: string; detail: string; model: string };
  providers: Record<AgentProvider, ProviderStatus>;
  game_data: {
    path: string;
    sha256: string;
    spatial_tables: {
      path: string;
      game_build: string;
      current: boolean;
      regenerated: boolean;
    };
  };
  save: null | {
    source_name: string;
    source_relative_path: string;
    source_size: number;
    source_mtime_ns: number;
    source_sha256: string;
    snapshot_path: string;
    created_at: string;
  };
  safety: {
    original_save_root: string;
    engine_save_root: string;
    originals_passed_to_parser: boolean;
    save_root_write_access: boolean;
    snapshot_only: boolean;
  };
  engine: { name: string; revision: string; license: string };
  warnings: string[];
}

export interface MapInfo {
  available: boolean;
  source: "installed_game" | null;
  bounds: {
    x_min_m: number;
    x_max_m: number;
    y_min_m: number;
    y_max_m: number;
  };
  tile_px: number;
  max_z: number;
  dense_tile_px: number | null;
  dense_max_z: number | null;
  version: string;
  reason: string | null;
}

export interface Machine {
  instance_leaf: string;
  cls: string;
  name: string;
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  recipe: string | null;
  recipe_name: string | null;
  clock: number | null;
  paused: boolean;
  state: string;
  uptime: number | null;
  yaw: number | null;
  w_m: number | null;
  l_m: number | null;
  h_m: number | null;
}

export interface Landmark {
  instance_leaf: string;
  cls: string;
  name: string;
  x_m: number;
  y_m: number;
  z_m: number;
  yaw: number | null;
  w_m: number | null;
  l_m: number | null;
  h_m: number | null;
}

export interface Structure {
  cls: string | null;
  x_m: number;
  y_m: number;
  z_m: number;
  yaw: number | null;
}

export interface StoragePlacement {
  instance_leaf: string;
  cls: string;
  name: string;
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  yaw: number | null;
  w_m: number | null;
  l_m: number | null;
  kind: "solid" | "fluid";
  platform: number | null;
  global_floor: number | null;
  top_m: number | null;
  floor_assignment: "foundation_exact" | "elevation_matched" | null;
  items?: Array<{ cls: string; name: string; count: number }>;
  total?: number;
}

export interface RouteSegment {
  points_m: Array<[number, number, number]>;
  name: string;
  fluid_name?: string | null;
  items_per_min?: number;
  flow_m3_min?: number | null;
  chain?: number;
  row?: number;
}

export interface FloorBand {
  ordinal: number;
  top_m: number | null;
  minor: boolean;
  area_m2: number;
  machines: string[];
  attachments: string[];
  deck_rows: number[];
  machine_count: number;
  attachment_count: number;
}

export interface FloorPlatform {
  index: number;
  area_m2: number;
  centre_m: [number | null, number | null];
  extent_m: [number | null, number | null];
  clean: number;
  label: string | null;
  bands: FloorBand[];
}

export interface FactoryProposal {
  index: number;
  label: string;
  centroid_m: [number, number];
  bbox_m: [number, number, number, number] | null;
  machines: number;
  score: number;
  spread_m: number;
}

export interface Workspace {
  generation: number;
  snapshot: Record<string, unknown>;
  summary: {
    header: Record<string, unknown> & {
      filename?: string;
      session_name?: string;
      build_version?: number;
      play_duration_s?: number;
    };
    save_token: string;
    age_note: string;
    power: {
      generation_mw: number;
      draw_mw: number;
      headroom_mw: number;
      measured_draw_mw: number;
      measured_headroom_mw: number;
      utilisation: number;
    };
    progression: {
      game_phase: string | null;
      target_phase: string | null;
      highest_complete_tier: number | null;
      purchased_schematics: number;
      available_recipes: number;
    };
    player: { x_m: number | null; y_m: number | null; z_m: number | null };
  };
  factories: {
    labels: Array<{
      name: string;
      centroid_m: [number, number];
      bbox_m: [number, number, number, number] | null;
      machines: number;
      notes: string;
    }>;
    proposals: FactoryProposal[];
  };
  machines: { machines: Machine[]; extractors: Machine[]; generators: Machine[] };
  landmarks: Landmark[];
  floors: {
    note: string | null;
    counts: Record<string, number | Record<string, number>>;
    platforms: FloorPlatform[];
    violations: unknown[];
  };
  belts: { belts: RouteSegment[]; count: number; chains: number };
  pipes: { pipes: RouteSegment[]; count: number; networks: number };
  structures: { structures: Structure[]; count: number; tile_m: number };
  storage: {
    storage: StoragePlacement[];
    count: number;
    filled: number;
    items_total: number;
  };
}

export interface PlanMetric {
  label: string;
  value: number | string;
  unit: string | null;
  status: VerificationStatus;
}

export interface PlanAction {
  id: string;
  kind:
    | "add"
    | "remove"
    | "move"
    | "reroute"
    | "set_recipe"
    | "change_clock"
    | "keep"
    | "manual_check";
  status: VerificationStatus;
  title: string;
  why: string;
  quantity: number | null;
  building: string | null;
  recipe: string | null;
  rate_per_min: number | null;
  from_floor: number | null;
  to_floor: number | null;
  site: string | null;
  coordinates: { x_m: number; y_m: number; z_m: number | null } | null;
  connections: string[];
  transfer_item: string | null;
  source_site: string | null;
  destination_site: string | null;
  transfer_purpose: "none" | "internal" | "raw_input" | "storage" | "production_input";
  authorization_quote: string | null;
  source_distance_m: number | null;
  transport_mode: "none" | "belt" | "pipe" | "vehicle" | "train" | "drone";
}

export interface PlanAnswer {
  headline: string;
  summary: string;
  overall_status: VerificationStatus;
  capacity_basis: "nameplate";
  factory_strategy: "same_factory" | "new_factory";
  save_token: string;
  target: {
    item: string | null;
    rate_per_min: number | null;
    site: string | null;
    factory: string | null;
    floor: number | null;
  };
  metrics: PlanMetric[];
  evidence: Array<{
    claim: string;
    status: VerificationStatus;
    source: string;
    detail: string;
  }>;
  assumptions: string[];
  blockers: string[];
  raw_inputs: Array<{
    item: string;
    rate_per_min: number;
    strategy:
      | "player_supplied"
      | "nameplate_spare"
      | "overclock"
      | "rebalanced_output"
      | "new_extractor";
    effect: string;
    sources: Array<{
      node_id: string;
      distance_m: number;
      rate_per_min: number;
      saved_clock_percent: number;
      final_clock_percent: number;
      power_shards: number;
      transport_mode: "belt" | "pipe" | "vehicle" | "train" | "drone";
    }>;
  }>;
  actions: PlanAction[];
  floors: Array<{
    floor: number | null;
    label: string;
    status: VerificationStatus;
    instructions: string[];
  }>;
  unlock_advice: Array<{
    name: string;
    kind: "hard_drive" | "milestone" | "mam" | "none";
    priority: "now" | "soon" | "later" | "skip";
    reason: string;
    used_by_current_plan: boolean;
  }>;
  follow_up: string | null;
}

export interface StreamEvent {
  type: "status" | "tool" | "answer" | "error";
  stage?: string;
  message?: string;
  data?: PlanAnswer;
}
