"use client";

import React, { useState } from 'react';

export interface CorrelationHeatmapProps {
  matrix: {
    columns: string[];
    values: number[][];
  };
}

export const CorrelationHeatmap: React.FC<CorrelationHeatmapProps> = ({ matrix }) => {
  const [hoveredCell, setHoveredCell] = useState<{ r: number; c: number; val: number } | null>(null);

  if (!matrix || !matrix.columns || matrix.columns.length === 0 || !matrix.values || matrix.values.length === 0) {
    return (
      <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4 min-h-[300px] flex items-center justify-center">
        <span className="text-[#9BA1B0] font-mono text-sm">No correlation data available</span>
      </div>
    );
  }

  const { columns, values } = matrix;
  const n = columns.length;

  const getColor = (val: number) => {
    if (val < 0) {
      const intensity = Math.abs(val);
      return `color-mix(in srgb, #F43F5E ${intensity * 100}%, #22252F)`;
    } else {
      const intensity = val;
      return `color-mix(in srgb, #34D399 ${intensity * 100}%, #22252F)`;
    }
  };

  const cellSize = 40;
  const marginX = 120;
  const marginY = 120;
  const width = n * cellSize + marginX;
  const height = n * cellSize + marginY;

  return (
    <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4 overflow-auto relative">
      <h3 className="label-mono text-[#E8EAF0] text-sm uppercase tracking-wider font-semibold mb-4">Correlation Matrix</h3>
      <div className="inline-block relative">
        <svg width={width} height={height} className="block text-xs font-sans">
          <g transform={`translate(${marginX}, ${marginY})`}>
            {columns.map((col, c) => (
              <text
                key={`col-${c}`}
                x={c * cellSize + cellSize / 2}
                y={-10}
                fill="#9BA1B0"
                textAnchor="start"
                transform={`rotate(-45, ${c * cellSize + cellSize / 2}, -10)`}
              >
                {col}
              </text>
            ))}

            {columns.map((row, r) => (
              <text
                key={`row-${r}`}
                x={-10}
                y={r * cellSize + cellSize / 2}
                fill="#9BA1B0"
                textAnchor="end"
                dominantBaseline="middle"
              >
                {row}
              </text>
            ))}

            {values.map((row, r) =>
              row.map((val, c) => (
                <g key={`${r}-${c}`}>
                  <rect
                    x={c * cellSize}
                    y={r * cellSize}
                    width={cellSize - 2}
                    height={cellSize - 2}
                    fill={getColor(val)}
                    rx={2}
                    onMouseEnter={() => setHoveredCell({ r, c, val })}
                    onMouseLeave={() => setHoveredCell(null)}
                    className="transition-colors duration-200 cursor-pointer"
                  />
                </g>
              ))
            )}
          </g>
        </svg>

        {hoveredCell && (
          <div 
            className="absolute bg-[#161923] border border-[#22252F] p-2 rounded shadow-lg text-[#E8EAF0] text-xs pointer-events-none z-10"
            style={{ 
              top: hoveredCell.r * cellSize + marginY + cellSize, 
              left: hoveredCell.c * cellSize + marginX + cellSize / 2,
              transform: 'translate(-50%, -100%)',
              marginTop: '-10px'
            }}
          >
            <div className="font-mono mb-1">{columns[hoveredCell.r]} × {columns[hoveredCell.c]}</div>
            <div className="flex justify-between gap-4">
              <span className="text-[#9BA1B0]">Correlation:</span>
              <span className="font-mono text-[#22D3EE]">{hoveredCell.val.toFixed(3)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
