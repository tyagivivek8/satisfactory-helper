import { Box, Crosshair, Focus, MapPin, Navigation, Route, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Machine, MapInfo, PlanAction, Structure } from "../types";
import type { FactoryCanvasProps } from "./FactoryCanvas";
import { Button } from "./ui/button";
import {
  collectFactorySceneData,
  structureShape,
  worldToScene,
  type FactorySceneData,
  type StructureKind,
} from "./factoryScene3dData";

const machineColors: Record<string, number> = {
  producing: 0x50c988,
  saturated: 0x34b8d6,
  blocked: 0xdba742,
  starved: 0xd4544d,
  intermittent: 0xd5b452,
  idle: 0x77736d,
  paused: 0x555555,
};

const actionColors: Record<PlanAction["kind"], number> = {
  add: 0xe45136,
  remove: 0x9f9b95,
  move: 0xe1ac3f,
  reroute: 0x3cbad6,
  set_recipe: 0xe45136,
  change_clock: 0xe1ac3f,
  keep: 0x55c68b,
  manual_check: 0xe1ac3f,
};

const structureColors: Record<StructureKind, number> = {
  foundation: 0x353332,
  ramp: 0x423e39,
  wall: 0x514b45,
  railing: 0x9b7651,
};

type MachineMesh = THREE.InstancedMesh;

function disposeMaterial(material: THREE.Material) {
  const candidate = material as THREE.Material & { map?: THREE.Texture | null };
  candidate.map?.dispose();
  material.dispose();
}

function disposeTree(root: THREE.Object3D) {
  root.traverse((object) => {
    const renderable = object as THREE.Mesh;
    renderable.geometry?.dispose();
    if (Array.isArray(renderable.material)) renderable.material.forEach(disposeMaterial);
    else if (renderable.material) disposeMaterial(renderable.material);
  });
  root.clear();
}

export function rampGeometry(): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(
      [
        -0.5, -0.5, -0.5,
        0.5, -0.5, -0.5,
        0.5, -0.5, 0.5,
        -0.5, -0.5, 0.5,
        -0.5, 0.5, -0.5,
        -0.5, 0.5, 0.5,
      ],
      3,
    ),
  );
  geometry.setIndex([
    0, 1, 2, 0, 2, 3,
    4, 2, 1, 4, 5, 2,
    0, 4, 1,
    3, 2, 5,
    0, 3, 5, 0, 5, 4,
  ]);
  geometry.computeVertexNormals();
  return geometry;
}

function instanceMatrix(
  position: THREE.Vector3,
  yaw: number,
  scale: THREE.Vector3,
): THREE.Matrix4 {
  const quaternion = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(0, 1, 0),
    (-yaw * Math.PI) / 180,
  );
  return new THREE.Matrix4().compose(position, quaternion, scale);
}

function addStructures(group: THREE.Group, structures: Structure[], data: FactorySceneData) {
  const rows = new Map<string, { kind: StructureKind; structures: Structure[] }>();
  for (const structure of structures) {
    const shape = structureShape(structure.cls);
    const key = `${shape.kind}:${shape.width}:${shape.height}:${shape.depth}`;
    const row = rows.get(key) ?? { kind: shape.kind, structures: [] };
    row.structures.push(structure);
    rows.set(key, row);
  }

  for (const row of rows.values()) {
    const sample = structureShape(row.structures[0]?.cls ?? null);
    const geometry = row.kind === "ramp" ? rampGeometry() : new THREE.BoxGeometry(1, 1, 1);
    const material = new THREE.MeshStandardMaterial({
      color: structureColors[row.kind],
      roughness: 0.88,
      metalness: row.kind === "railing" ? 0.34 : 0.08,
    });
    const mesh = new THREE.InstancedMesh(geometry, material, row.structures.length);
    mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
    row.structures.forEach((structure, index) => {
      const [x, z, y] = worldToScene(structure.x_m, structure.y_m, structure.z_m, data);
      mesh.setMatrixAt(
        index,
        instanceMatrix(
          new THREE.Vector3(x, z + sample.verticalOffset, y),
          structure.yaw ?? 0,
          new THREE.Vector3(sample.width, sample.height, sample.depth),
        ),
      );
    });
    mesh.instanceMatrix.needsUpdate = true;
    group.add(mesh);
  }
}

function addMachines(
  group: THREE.Group,
  machines: Machine[],
  data: FactorySceneData,
  selectableMeshes: Map<MachineMesh, Machine[]>,
) {
  const rows = new Map<string, { kind: "factory" | "extractor" | "generator"; machines: Machine[] }>();
  for (const machine of machines) {
    const state = machine.paused ? "paused" : machine.state;
    const kind = /Miner|Pump|Extractor/i.test(machine.cls)
      ? "extractor"
      : /Generator|BiomassBurner/i.test(machine.cls)
        ? "generator"
        : "factory";
    const key = `${kind}:${state}`;
    const row = rows.get(key) ?? { kind, machines: [] };
    row.machines.push(machine);
    rows.set(key, row);
  }

  for (const [key, row] of rows) {
    const state = key.split(":", 2)[1];
    const geometry = row.kind === "extractor"
      ? new THREE.CylinderGeometry(0.5, 0.5, 1, 10)
      : new THREE.BoxGeometry(1, 1, 1);
    const material = new THREE.MeshStandardMaterial({
      color: row.kind === "extractor"
        ? 0xe29a45
        : row.kind === "generator"
          ? 0x5f9fc7
          : machineColors[state] ?? machineColors.idle,
      roughness: 0.55,
      metalness: 0.22,
    });
    const mesh = new THREE.InstancedMesh(geometry, material, row.machines.length);
    mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
    row.machines.forEach((machine, index) => {
      const width = Math.max(machine.w_m ?? 6, 3);
      const height = Math.max(machine.h_m ?? 5, 2);
      const depth = Math.max(machine.l_m ?? 8, 3);
      const [x, z, y] = worldToScene(machine.x_m!, machine.y_m!, machine.z_m!, data);
      mesh.setMatrixAt(
        index,
        instanceMatrix(
          new THREE.Vector3(x, z + height / 2, y),
          machine.yaw ?? 0,
          new THREE.Vector3(width, height, depth),
        ),
      );
    });
    mesh.instanceMatrix.needsUpdate = true;
    selectableMeshes.set(mesh, row.machines);
    group.add(mesh);
  }
}

function addSelectedMachine(
  group: THREE.Group,
  selected: Machine | null,
  machines: Machine[],
  data: FactorySceneData,
) {
  if (
    selected &&
    selected.x_m !== null &&
    selected.y_m !== null &&
    selected.z_m !== null &&
    machines.some((machine) => machine.instance_leaf === selected.instance_leaf)
  ) {
    const width = Math.max(selected.w_m ?? 6, 3) + 1;
    const height = Math.max(selected.h_m ?? 5, 2) + 1;
    const depth = Math.max(selected.l_m ?? 8, 3) + 1;
    const [x, z, y] = worldToScene(
      selected.x_m,
      selected.y_m,
      selected.z_m,
      data,
    );
    const outline = new THREE.Mesh(
      new THREE.BoxGeometry(width, height, depth),
      new THREE.MeshBasicMaterial({ color: 0xf3eee0, wireframe: true }),
    );
    outline.position.set(x, z + height / 2 - 0.5, y);
    outline.rotation.y = (-(selected.yaw ?? 0) * Math.PI) / 180;
    group.add(outline);
  }
}

function addStorage(group: THREE.Group, data: FactorySceneData) {
  if (!data.storage.length) return;
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const material = new THREE.MeshStandardMaterial({
    color: 0x8e6ab8,
    roughness: 0.58,
    metalness: 0.18,
  });
  const mesh = new THREE.InstancedMesh(geometry, material, data.storage.length);
  data.storage.forEach((storage, index) => {
    const width = Math.max(storage.w_m ?? 5, 3);
    const height = storage.kind === "fluid" ? 8 : 4;
    const depth = Math.max(storage.l_m ?? 10, 3);
    const [x, z, y] = worldToScene(storage.x_m!, storage.y_m!, storage.z_m!, data);
    mesh.setMatrixAt(
      index,
      instanceMatrix(
        new THREE.Vector3(x, z + height / 2, y),
        storage.yaw ?? 0,
        new THREE.Vector3(width, height, depth),
      ),
    );
  });
  mesh.instanceMatrix.needsUpdate = true;
  group.add(mesh);
}

function beamBetween(
  start: THREE.Vector3,
  end: THREE.Vector3,
  radius: number,
  material: THREE.Material,
): THREE.Mesh {
  const direction = end.clone().sub(start);
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, 1, 8), material);
  beam.position.copy(start).add(end).multiplyScalar(0.5);
  beam.scale.y = direction.length();
  beam.quaternion.setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    direction.normalize(),
  );
  return beam;
}

function addLandmarks(group: THREE.Group, data: FactorySceneData) {
  for (const landmark of data.landmarks) {
    const [x, z, y] = worldToScene(landmark.x_m, landmark.y_m, landmark.z_m, data);
    if (landmark.cls !== "Build_SpaceElevator_C") {
      const width = landmark.w_m ?? 20;
      const depth = landmark.l_m ?? 20;
      const height = landmark.h_m ?? 20;
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(width, height, depth),
        new THREE.MeshStandardMaterial({ color: 0x82735f, roughness: 0.7 }),
      );
      mesh.position.set(x, z + height / 2, y);
      mesh.rotation.y = (-(landmark.yaw ?? 0) * Math.PI) / 180;
      group.add(mesh);
      continue;
    }

    const elevator = new THREE.Group();
    elevator.position.set(x, z, y);
    elevator.rotation.y = (-(landmark.yaw ?? 0) * Math.PI) / 180;
    elevator.userData.landmark = landmark.instance_leaf;

    const frame = new THREE.MeshStandardMaterial({
      color: 0x8e7b63,
      roughness: 0.54,
      metalness: 0.48,
    });
    const dark = new THREE.MeshStandardMaterial({
      color: 0x302e2b,
      roughness: 0.66,
      metalness: 0.35,
    });
    const accent = new THREE.MeshStandardMaterial({
      color: 0xe29a45,
      roughness: 0.48,
      metalness: 0.38,
    });

    const pad = new THREE.Mesh(new THREE.CylinderGeometry(24, 26, 2.4, 12), dark);
    pad.position.y = 1.2;
    elevator.add(pad);

    const cargoDeck = new THREE.Mesh(new THREE.BoxGeometry(31, 4, 25), frame);
    cargoDeck.position.y = 4.1;
    elevator.add(cargoDeck);

    const core = new THREE.Mesh(new THREE.CylinderGeometry(3.4, 5.2, 56, 12), dark);
    core.position.y = 32;
    elevator.add(core);

    for (let index = 0; index < 3; index += 1) {
      const angle = (index * Math.PI * 2) / 3;
      const outer = new THREE.Vector3(Math.cos(angle) * 20, 2.5, Math.sin(angle) * 20);
      const inner = new THREE.Vector3(Math.cos(angle) * 6, 35, Math.sin(angle) * 6);
      elevator.add(beamBetween(outer, inner, 1.35, frame));

      const arm = new THREE.Mesh(new THREE.BoxGeometry(18, 2.2, 4.2), accent);
      arm.position.set(Math.cos(angle) * 12, 8, Math.sin(angle) * 12);
      arm.rotation.y = -angle;
      elevator.add(arm);
    }

    for (const [radius, height] of [[10, 18], [7.5, 37], [5.5, 55]] as const) {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.7, 8, 32), accent);
      ring.rotation.x = Math.PI / 2;
      ring.position.y = height;
      elevator.add(ring);
    }

    const tether = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.7, 55, 8), accent);
    tether.position.y = 82.5;
    elevator.add(tether);
    for (let index = 0; index < 3; index += 1) {
      const angle = (index * Math.PI * 2) / 3;
      elevator.add(
        beamBetween(
          new THREE.Vector3(Math.cos(angle) * 5.3, 54, Math.sin(angle) * 5.3),
          new THREE.Vector3(0, 108, 0),
          0.18,
          frame,
        ),
      );
    }

    group.add(elevator);
  }
}

function addRoutes(
  group: THREE.Group,
  routes: FactorySceneData["belts"],
  data: FactorySceneData,
  color: number,
) {
  const positions: number[] = [];
  for (const route of routes) {
    for (let index = 1; index < route.points_m.length; index += 1) {
      const start = route.points_m[index - 1];
      const end = route.points_m[index];
      positions.push(...worldToScene(start[0], start[1], start[2] + 0.22, data));
      positions.push(...worldToScene(end[0], end[1], end[2] + 0.22, data));
    }
  }
  if (!positions.length) return;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const material = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.88 });
  group.add(new THREE.LineSegments(geometry, material));
}

function actionZ(
  action: PlanAction,
  data: FactorySceneData,
  platform: FactoryCanvasProps["platform"],
  selectedFloor: number | null,
): number {
  if (action.coordinates?.z_m !== null && action.coordinates?.z_m !== undefined) {
    return action.coordinates.z_m;
  }
  const actionFloor = action.to_floor ?? action.from_floor ?? selectedFloor;
  const floorTop = platform?.bands.find((band) => band.ordinal === actionFloor)?.top_m;
  return floorTop ?? data.minZ;
}

function addPlanMarkers(
  group: THREE.Group,
  actions: PlanAction[],
  activeAction: PlanAction | null,
  data: FactorySceneData,
  platform: FactoryCanvasProps["platform"],
  selectedFloor: number | null,
) {
  for (const action of actions) {
    if (!action.coordinates) continue;
    const active = action.id === activeAction?.id;
    const [x, z, y] = worldToScene(
      action.coordinates.x_m,
      action.coordinates.y_m,
      actionZ(action, data, platform, selectedFloor),
      data,
    );
    const material = new THREE.MeshBasicMaterial({
      color: actionColors[action.kind],
      transparent: true,
      opacity: active ? 1 : 0.8,
    });
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(active ? 7 : 5, active ? 0.9 : 0.55, 8, 32),
      material,
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.set(x, z + 1.2, y);
    group.add(ring);

    if (active) {
      const localGrid = new THREE.GridHelper(120, 12, actionColors[action.kind], 0x504841);
      const gridMaterial = localGrid.material as THREE.LineBasicMaterial;
      gridMaterial.transparent = true;
      gridMaterial.opacity = 0.42;
      gridMaterial.depthWrite = false;
      localGrid.position.set(x, z + 0.08, y);
      group.add(localGrid);

      const beacon = new THREE.Mesh(
        new THREE.CylinderGeometry(0.35, 0.35, 24, 8),
        material.clone(),
      );
      beacon.position.set(x, z + 13, y);
      group.add(beacon);
    }
  }
}

export function sceneMapZoom(
  mapInfo: MapInfo,
  visibleWorldSpan: number,
  viewportPixels: number,
): number {
  const worldWidth = mapInfo.bounds.x_max_m - mapInfo.bounds.x_min_m;
  const pixelsAcrossWorld =
    worldWidth * (Math.max(1, viewportPixels) / Math.max(500, visibleWorldSpan));
  const resolutionZoom = Math.ceil(
    Math.log2(Math.max(1, pixelsAcrossWorld / mapInfo.tile_px)),
  );
  return Math.max(0, Math.min(mapInfo.max_z, 5, resolutionZoom));
}

function addMapTiles(
  group: THREE.Group,
  mapInfo: MapInfo,
  data: FactorySceneData,
  platform: FactoryCanvasProps["platform"],
  extraPoints: Array<{ x_m: number; y_m: number }>,
  renderer: THREE.WebGLRenderer,
  render: () => void,
): () => void {
  if (
    !mapInfo.available ||
    !platform ||
    platform.centre_m[0] === null ||
    platform.centre_m[1] === null
  ) {
    return () => undefined;
  }
  const zoom = sceneMapZoom(
    mapInfo,
    data.contentSpan + 240,
    renderer.domElement.clientWidth || 900,
  );
  const span = 1 << zoom;
  const tileWidth = (mapInfo.bounds.x_max_m - mapInfo.bounds.x_min_m) / span;
  const tileDepth = (mapInfo.bounds.y_max_m - mapInfo.bounds.y_min_m) / span;
  const extentX = platform.extent_m[0] ?? data.horizontalSpan;
  const extentY = platform.extent_m[1] ?? data.horizontalSpan;
  const tiles = new Set<string>();
  function includeBounds(left: number, right: number, top: number, bottom: number) {
    const minX = Math.max(0, Math.floor((left - mapInfo.bounds.x_min_m) / tileWidth));
    const maxX = Math.min(span - 1, Math.floor((right - mapInfo.bounds.x_min_m) / tileWidth));
    const minY = Math.max(0, Math.floor((top - mapInfo.bounds.y_min_m) / tileDepth));
    const maxY = Math.min(span - 1, Math.floor((bottom - mapInfo.bounds.y_min_m) / tileDepth));
    for (let tileY = minY; tileY <= maxY; tileY += 1) {
      for (let tileX = minX; tileX <= maxX; tileX += 1) {
        tiles.add(`${tileX}:${tileY}`);
      }
    }
  }
  includeBounds(
    platform.centre_m[0] - extentX / 2 - 120,
    platform.centre_m[0] + extentX / 2 + 120,
    platform.centre_m[1] - extentY / 2 - 120,
    platform.centre_m[1] + extentY / 2 + 120,
  );
  for (const point of extraPoints) {
    includeBounds(point.x_m - 160, point.x_m + 160, point.y_m - 160, point.y_m + 160);
  }
  const loader = new THREE.TextureLoader();
  let cancelled = false;

  for (const tile of tiles) {
      const [tileX, tileY] = tile.split(":").map(Number);
      loader.load(
        `/api/maptiles/${zoom}/${tileX}/${tileY}?v=${mapInfo.version}`,
        (texture) => {
          if (cancelled) {
            texture.dispose();
            return;
          }
          texture.colorSpace = THREE.SRGBColorSpace;
          texture.anisotropy = Math.min(renderer.capabilities.getMaxAnisotropy(), 8);
          const material = new THREE.MeshBasicMaterial({
            map: texture,
            transparent: true,
            opacity: 0.72,
            depthWrite: false,
          });
          const mesh = new THREE.Mesh(new THREE.PlaneGeometry(tileWidth, tileDepth), material);
          const worldX = mapInfo.bounds.x_min_m + (tileX + 0.5) * tileWidth;
          const worldY = mapInfo.bounds.y_min_m + (tileY + 0.5) * tileDepth;
          mesh.rotation.x = -Math.PI / 2;
          mesh.position.set(
            worldX - data.originX,
            data.minZ - 4.8,
            worldY - data.originY,
          );
          group.add(mesh);
          render();
        },
        undefined,
        () => undefined,
      );
  }
  return () => {
    cancelled = true;
  };
}

export function FactoryScene3D({
  workspace,
  mapInfo,
  platform,
  selectedFloor,
  plan,
  activeAction,
  selectedMachine,
  onSelectMachine,
}: FactoryCanvasProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const compassRef = useRef<HTMLSpanElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const contentRef = useRef<THREE.Group | null>(null);
  const groundRef = useRef<THREE.Group | null>(null);
  const mapRef = useRef<THREE.Group | null>(null);
  const planOverlayRef = useRef<THREE.Group | null>(null);
  const selectionOverlayRef = useRef<THREE.Group | null>(null);
  const machineMeshesRef = useRef<Map<MachineMesh, Machine[]>>(new Map());
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const selectMachineRef = useRef(onSelectMachine);
  const fitRef = useRef<() => void>(() => undefined);
  const renderRef = useRef<() => void>(() => undefined);
  const [error, setError] = useState<string | null>(null);

  const data = useMemo(
    () => collectFactorySceneData(workspace, platform, selectedFloor),
    [platform, selectedFloor, workspace],
  );

  useEffect(() => {
    selectMachineRef.current = onSelectMachine;
  }, [onSelectMachine]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance" });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "WebGL could not start.");
      return;
    }

    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute(
      "aria-label",
      "Interactive 3D factory layout. Left drag to orbit, right drag to pan, and scroll to zoom.",
    );
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x11100f);
    scene.fog = new THREE.FogExp2(0x11100f, 0.00033);
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 20_000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.screenSpacePanning = false;
    controls.minPolarAngle = 0.08;
    controls.maxPolarAngle = Math.PI / 2.02;
    controls.minDistance = 12;
    controls.maxDistance = 9_000;

    const hemisphere = new THREE.HemisphereLight(0xd9e4ef, 0x2c2118, 1.9);
    const keyLight = new THREE.DirectionalLight(0xffefd7, 2.5);
    keyLight.position.set(-320, 480, 260);
    scene.add(hemisphere, keyLight);

    const ground = new THREE.Group();
    const map = new THREE.Group();
    const content = new THREE.Group();
    const planOverlay = new THREE.Group();
    const selectionOverlay = new THREE.Group();
    scene.add(ground, map, content, planOverlay, selectionOverlay);

    function updateCompass() {
      if (!compassRef.current) return;
      const target = controls.target.clone().project(camera);
      const north = controls.target.clone().add(new THREE.Vector3(0, 0, -100)).project(camera);
      const angle = Math.atan2(north.x - target.x, north.y - target.y);
      compassRef.current.style.setProperty("--north-rotation", `${angle}rad`);
    }

    function render() {
      renderer.render(scene, camera);
      updateCompass();
    }
    renderRef.current = render;
    controls.addEventListener("change", render);

    const resize = new ResizeObserver(([entry]) => {
      const width = Math.max(1, entry.contentRect.width);
      const height = Math.max(1, entry.contentRect.height);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      render();
    });
    resize.observe(mount);

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const handlePointerDown = (event: PointerEvent) => {
      pointerDownRef.current = { x: event.clientX, y: event.clientY };
    };
    const handlePointerUp = (event: PointerEvent) => {
      const start = pointerDownRef.current;
      pointerDownRef.current = null;
      if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5) return;
      const bounds = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
      pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const meshes = [...machineMeshesRef.current.keys()];
      const hit = raycaster.intersectObjects(meshes, false)[0];
      if (!hit || hit.instanceId === undefined) {
        selectMachineRef.current(null);
        return;
      }
      const machines = machineMeshesRef.current.get(hit.object as MachineMesh);
      selectMachineRef.current(machines?.[hit.instanceId] ?? null);
    };
    renderer.domElement.addEventListener("pointerdown", handlePointerDown);
    renderer.domElement.addEventListener("pointerup", handlePointerUp);

    sceneRef.current = scene;
    rendererRef.current = renderer;
    cameraRef.current = camera;
    controlsRef.current = controls;
    contentRef.current = content;
    groundRef.current = ground;
    mapRef.current = map;
    planOverlayRef.current = planOverlay;
    selectionOverlayRef.current = selectionOverlay;
    render();

    return () => {
      resize.disconnect();
      renderer.domElement.removeEventListener("pointerdown", handlePointerDown);
      renderer.domElement.removeEventListener("pointerup", handlePointerUp);
      controls.removeEventListener("change", render);
      controls.dispose();
      disposeTree(content);
      disposeTree(ground);
      disposeTree(map);
      disposeTree(planOverlay);
      disposeTree(selectionOverlay);
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
      rendererRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      contentRef.current = null;
      groundRef.current = null;
      mapRef.current = null;
      planOverlayRef.current = null;
      selectionOverlayRef.current = null;
    };
  }, []);

  useEffect(() => {
    const renderer = rendererRef.current;
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    const content = contentRef.current;
    const ground = groundRef.current;
    if (!renderer || !camera || !controls || !content || !ground) return;
    const viewCamera = camera;
    const viewControls = controls;

    disposeTree(content);
    disposeTree(ground);
    machineMeshesRef.current.clear();

    const gridSize = Math.max(240, Math.ceil((data.contentSpan + 160) / 40) * 40);
    const groundY = data.minZ - 5;
    const minorGrid = new THREE.GridHelper(
      gridSize,
      Math.max(10, Math.round(gridSize / 8)),
      0x3b3733,
      0x292725,
    );
    const majorGrid = new THREE.GridHelper(
      gridSize,
      Math.max(4, Math.round(gridSize / 40)),
      0x6b5544,
      0x4a4139,
    );
    minorGrid.position.set(data.focusX, groundY, data.focusY);
    majorGrid.position.set(data.focusX, groundY + 0.03, data.focusY);
    for (const grid of [minorGrid, majorGrid]) {
      const material = grid.material as THREE.LineBasicMaterial;
      material.transparent = true;
      material.opacity = grid === minorGrid ? 0.24 : 0.42;
      material.depthWrite = false;
      ground.add(grid);
    }

    addStructures(content, data.structures, data);
    addLandmarks(content, data);
    addStorage(content, data);
    addRoutes(content, data.belts, data, 0xa88a62);
    addRoutes(content, data.pipes, data, 0x42b9d4);
    addMachines(content, data.machines, data, machineMeshesRef.current);

    function fit() {
      const verticalSpan = Math.max(20, data.maxZ - data.minZ);
      const focusSpan = Math.max(data.contentSpan, verticalSpan * 2.6, 120);
      const distance = (focusSpan / (2 * Math.tan(THREE.MathUtils.degToRad(viewCamera.fov / 2)))) * 1.04;
      const targetY = (data.minZ + data.maxZ) / 2;
      viewControls.target.set(data.focusX, targetY, data.focusY);
      viewCamera.position.set(
        data.focusX + distance * 0.62,
        targetY + distance * 0.42,
        data.focusY + distance * 0.68,
      );
      viewCamera.near = Math.max(0.1, distance / 2_000);
      viewCamera.far = Math.max(4_000, distance * 8);
      viewCamera.updateProjectionMatrix();
      viewControls.update();
      renderRef.current();
    }
    fitRef.current = fit;
    fit();

  }, [data, mapInfo, platform]);

  useEffect(() => {
    const renderer = rendererRef.current;
    const map = mapRef.current;
    if (!renderer || !map) return;
    disposeTree(map);
    if (!mapInfo) return;
    const extraPoints = (plan?.actions ?? []).flatMap((action) =>
      action.coordinates
        ? [{ x_m: action.coordinates.x_m, y_m: action.coordinates.y_m }]
        : [],
    );
    const stopMapLoad = addMapTiles(
      map,
      mapInfo,
      data,
      platform,
      extraPoints,
      renderer,
      renderRef.current,
    );
    return stopMapLoad;
  }, [data, mapInfo, plan, platform]);

  useEffect(() => {
    const overlay = planOverlayRef.current;
    if (!overlay) return;
    disposeTree(overlay);
    addPlanMarkers(overlay, plan?.actions ?? [], activeAction, data, platform, selectedFloor);
    renderRef.current();
  }, [activeAction, data, plan, platform, selectedFloor]);

  useEffect(() => {
    const overlay = selectionOverlayRef.current;
    if (!overlay) return;
    disposeTree(overlay);
    addSelectedMachine(overlay, selectedMachine, data.machines, data);
    renderRef.current();
  }, [data, selectedMachine]);

  function focusActiveAction() {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls || !activeAction?.coordinates) return;
    const [x, z, y] = worldToScene(
      activeAction.coordinates.x_m,
      activeAction.coordinates.y_m,
      actionZ(activeAction, data, platform, selectedFloor),
      data,
    );
    const distance = 150;
    controls.target.set(x, z + 3, y);
    camera.position.set(x + distance * 0.62, z + distance * 0.48, y + distance * 0.68);
    camera.near = 0.1;
    camera.far = Math.max(4_000, distance * 8);
    camera.updateProjectionMatrix();
    controls.update();
    renderRef.current();
  }

  if (error) {
    return (
      <div className="scene-error" role="status">
        <Box aria-hidden="true" size={18} />
        <strong>3D view could not start</strong>
        <span>{error}</span>
        <span>Switch to 2D above to keep using the map.</span>
      </div>
    );
  }

  return (
    <div className="canvas-shell canvas-shell--3d">
      <div ref={mountRef} className="scene-mount" />
      <div className="canvas-corner canvas-corner--left scene-readouts">
        <span><Crosshair aria-hidden="true" size={14} /> Measured XYZ geometry</span>
        <span><Box aria-hidden="true" size={14} /> {data.structures.length} structures · {data.machines.length} machines · {data.landmarks.length} landmarks</span>
        <span className="canvas-route-readout"><Route aria-hidden="true" size={14} /> {data.belts.length} belts · {data.pipes.length} pipes</span>
      </div>
      <div className="canvas-corner canvas-corner--right">
        <span ref={compassRef} className="north-indicator scene-compass">
          <Navigation aria-hidden="true" size={14} /> N
        </span>
        {activeAction?.coordinates && (
          <Button type="button" size="sm" variant="outline" onClick={focusActiveAction}>
            <MapPin aria-hidden="true" size={15} /> Focus change
          </Button>
        )}
        <Button type="button" size="sm" variant="outline" onClick={() => fitRef.current()}>
          <Focus aria-hidden="true" size={15} /> Fit 3D
        </Button>
      </div>
      <div className="scene-controls-hint">Left drag orbit · Right drag pan · Scroll zoom</div>
      {selectedMachine && (
        <aside className="machine-inspector" aria-live="polite">
          <div><ScanSearch aria-hidden="true" size={15} /><strong>{selectedMachine.name}</strong></div>
          <span>{selectedMachine.recipe_name ?? "No recipe"}</span>
          <dl>
            <div><dt>State</dt><dd>{selectedMachine.paused ? "paused" : selectedMachine.state}</dd></div>
            <div><dt>Clock</dt><dd>{Math.round((selectedMachine.clock ?? 1) * 100)}%</dd></div>
            <div><dt>XYZ</dt><dd>{selectedMachine.x_m?.toFixed(1)}, {selectedMachine.y_m?.toFixed(1)}, {selectedMachine.z_m?.toFixed(1)}</dd></div>
          </dl>
        </aside>
      )}
    </div>
  );
}
