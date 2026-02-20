/**
 * Mini map component - shows current location and available destinations
 */
import React, { useMemo } from 'react';
import { Map, MapPin, Compass } from 'lucide-react';
import { useGameStore } from '../../stores';
import { useStreamGameInput } from '../../api';
import type { MapGraphNode } from '../../types';

interface MiniMapProps {
  className?: string;
}

const VIEWBOX_SIZE = 100;
const CENTER = VIEWBOX_SIZE / 2;
const RING_ONE_RADIUS = 28;
const RING_TWO_RADIUS = 42;
const NODE_RADIUS = 3.4;

const dangerColors = {
  low: 'var(--g-danger-low)',
  medium: 'var(--g-danger-medium)',
  high: 'var(--g-danger-high)',
  extreme: 'var(--g-danger-extreme)',
};

const dangerLabels = {
  low: 'Safe',
  medium: 'Moderate',
  high: 'Dangerous',
  extreme: 'Deadly',
};

function truncateLabel(value: string, maxLength = 8): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 1)}…`;
}

function sortNodesStable(a: MapGraphNode, b: MapGraphNode): number {
  return a.id.localeCompare(b.id);
}

function placeRing(
  nodeIds: string[],
  radius: number,
  startDeg: number,
  output: Record<string, { x: number; y: number }>,
) {
  if (nodeIds.length === 0) return;
  const step = (Math.PI * 2) / nodeIds.length;
  const start = (startDeg * Math.PI) / 180;
  for (let i = 0; i < nodeIds.length; i += 1) {
    const angle = start + step * i;
    output[nodeIds[i]] = {
      x: CENTER + Math.cos(angle) * radius,
      y: CENTER + Math.sin(angle) * radius,
    };
  }
}

export const MiniMap: React.FC<MiniMapProps> = ({ className = '' }) => {
  const { mapGraph, location } = useGameStore();
  const { sendInput, isLoading } = useStreamGameInput();
  const graphNodes = useMemo(() => Object.values(mapGraph.nodes), [mapGraph.nodes]);
  const currentNodeId = useMemo(() => {
    const currentNode = graphNodes.find((node) => node.is_current);
    if (currentNode) return currentNode.id;
    if (location?.location_id) return location.location_id;
    if (graphNodes.length === 0) return null;
    return [...graphNodes].sort((a, b) => b.last_seen_at - a.last_seen_at)[0].id;
  }, [graphNodes, location?.location_id]);

  const positions = useMemo(() => {
    const result: Record<string, { x: number; y: number }> = {};
    if (!currentNodeId) return result;

    result[currentNodeId] = { x: CENTER, y: CENTER };

    const ringOneNodes = graphNodes
      .filter((node) => node.id !== currentNodeId && node.is_reachable_from_current)
      .sort(sortNodesStable)
      .map((node) => node.id);
    const ringTwoNodes = graphNodes
      .filter((node) => node.id !== currentNodeId && !node.is_reachable_from_current)
      .sort(sortNodesStable)
      .map((node) => node.id);

    placeRing(
      ringOneNodes,
      ringOneNodes.length <= 3 ? 24 : RING_ONE_RADIUS,
      -90,
      result,
    );
    placeRing(ringTwoNodes, RING_TWO_RADIUS, -78, result);

    return result;
  }, [currentNodeId, graphNodes]);

  const edges = useMemo(
    () =>
      Object.values(mapGraph.edges).filter(
        (edge) => positions[edge.from] && positions[edge.to],
      ),
    [mapGraph.edges, positions],
  );
  const visibleNodes = useMemo(
    () =>
      graphNodes
        .filter((node) => positions[node.id])
        .sort((a, b) => {
          if (a.is_current && !b.is_current) return 1;
          if (!a.is_current && b.is_current) return -1;
          return sortNodesStable(a, b);
        }),
    [graphNodes, positions],
  );

  const unlockedNodeCount = visibleNodes.filter((node) => node.is_unlocked).length;
  const lockedNodeCount = visibleNodes.length - unlockedNodeCount;

  const handleNodeClick = (node: MapGraphNode) => {
    if (!currentNodeId) return;
    if (node.id === currentNodeId) return;
    if (!node.is_unlocked || isLoading) return;
    sendInput(`前往${node.name}`);
  };

  return (
    <div className={`p-3 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Map className="w-4 h-4 text-g-gold" />
          <h3 className="text-sm font-heading text-g-gold">Map</h3>
        </div>
        <Compass className="w-4 h-4 text-[var(--g-text-muted)]" />
      </div>

      {/* Map area */}
      <div
        className="
          relative
          aspect-square
          bg-g-bg-sidebar
          rounded-xl
          overflow-hidden
          border-2 border-g-border-strong
        "
      >
        {/* Grid pattern */}
        <div
          className="absolute inset-0 opacity-40"
          style={{
            backgroundImage: `
              linear-gradient(rgba(196, 154, 42, 0.08) 1px, transparent 1px),
              linear-gradient(90deg, rgba(196, 154, 42, 0.08) 1px, transparent 1px)
            `,
            backgroundSize: '20px 20px',
          }}
        />

        {/* Graph */}
        {visibleNodes.length > 0 && (
          <svg
            viewBox={`0 0 ${VIEWBOX_SIZE} ${VIEWBOX_SIZE}`}
            className="absolute inset-0 w-full h-full"
          >
            {edges.map((edge) => {
              const from = positions[edge.from];
              const to = positions[edge.to];
              return (
                <line
                  key={edge.id}
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke={edge.is_active ? 'var(--g-accent-gold)' : 'var(--g-border-strong)'}
                  strokeWidth={edge.is_active ? 1.2 : 0.8}
                  strokeDasharray={edge.is_active ? undefined : '2 2'}
                  opacity={edge.is_active ? 0.9 : 0.5}
                />
              );
            })}

            {visibleNodes.map((node) => {
              const pos = positions[node.id];
              const isCurrent = node.id === currentNodeId;
              const isClickable = !isCurrent && node.is_unlocked && !isLoading;
              const directEdge = currentNodeId
                ? mapGraph.edges[`${currentNodeId}->${node.id}`]
                : null;
              const titleParts = [
                node.name,
                `Danger: ${dangerLabels[node.danger_level]}`,
              ];
              if (directEdge?.travel_time) {
                titleParts.push(`Travel: ${directEdge.travel_time}`);
              }

              return (
                <g
                  key={node.id}
                  onClick={() => handleNodeClick(node)}
                  style={{ cursor: isClickable ? 'pointer' : 'default' }}
                >
                  <title>{titleParts.join(' | ')}</title>
                  <circle
                    cx={pos.x}
                    cy={pos.y}
                    r={NODE_RADIUS + 1.8}
                    fill="transparent"
                    stroke={dangerColors[node.danger_level]}
                    strokeWidth={isCurrent ? 2 : 1.2}
                    opacity={node.is_unlocked ? 0.95 : 0.35}
                  />
                  <circle
                    cx={pos.x}
                    cy={pos.y}
                    r={NODE_RADIUS}
                    fill={
                      isCurrent
                        ? 'var(--g-accent-gold)'
                        : node.is_unlocked
                        ? 'var(--g-cyan)'
                        : 'var(--g-text-muted)'
                    }
                    stroke={isCurrent ? '#FFFFFF' : 'var(--g-bg-surface)'}
                    strokeWidth={isCurrent ? 1.2 : 0.8}
                    opacity={node.is_unlocked ? 1 : 0.5}
                  />
                  {isCurrent && (
                    <circle
                      cx={pos.x}
                      cy={pos.y}
                      r={NODE_RADIUS + 4}
                      fill="transparent"
                      stroke="var(--g-accent-gold)"
                      strokeWidth={0.8}
                      opacity={0.45}
                    />
                  )}
                  <text
                    x={pos.x}
                    y={Math.min(pos.y + NODE_RADIUS + 5.5, VIEWBOX_SIZE - 2)}
                    textAnchor="middle"
                    fontSize="3.4"
                    fill={isCurrent ? 'var(--g-accent-gold-dark)' : 'var(--g-text-secondary)'}
                  >
                    {truncateLabel(node.name)}
                  </text>
                </g>
              );
            })}
          </svg>
        )}

        {/* Location name overlay */}
        {location && (
          <div
            className="
              absolute bottom-2 left-2 right-2
              bg-g-bg-base/80
              backdrop-blur-sm
              rounded-lg
              p-2
              text-center
            "
          >
            <div className="flex items-center justify-center gap-1">
              <MapPin className="w-3 h-3 text-g-gold" />
              <span className="text-xs font-medium text-[var(--g-text-primary)] truncate">
                {location.location_name}
              </span>
            </div>
          </div>
        )}

        {/* Placeholder text if no location */}
        {!location && visibleNodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-xs text-[var(--g-text-muted)]">
              No map data
            </span>
          </div>
        )}
      </div>

      {/* Mini legend */}
      <div className="mt-2 flex items-center justify-center gap-4 text-xs text-[var(--g-text-muted)]">
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-full bg-g-gold" />
          <span>Current</span>
        </div>
        {visibleNodes.length > 1 && (
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-g-cyan" />
            <span>Discovered</span>
          </div>
        )}
        {lockedNodeCount > 0 && (
          <div className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full bg-[var(--g-text-muted)] opacity-60" />
            <span>Locked</span>
          </div>
        )}
      </div>

      {(unlockedNodeCount > 0 || lockedNodeCount > 0) && (
        <div className="mt-1 text-[10px] text-[var(--g-text-muted)] text-center">
          {unlockedNodeCount} unlocked · {lockedNodeCount} locked
        </div>
      )}
    </div>
  );
};

export default MiniMap;
