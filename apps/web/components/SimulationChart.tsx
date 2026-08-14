"use client";

import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend
} from 'recharts';

export interface SimulationChartProps {
  segments: {
    segment: string;
    baseline: number;
    simulated: number;
    change_pct: number;
  }[];
}

export const SimulationChart: React.FC<SimulationChartProps> = ({ segments }) => {
  if (!segments || segments.length === 0) {
    return (
      <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4 min-h-[300px] flex items-center justify-center">
        <span className="text-[#9BA1B0] font-mono text-sm">No simulation data available</span>
      </div>
    );
  }

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="bg-[#161923] border border-[#22252F] p-3 rounded-md shadow-lg text-[#E8EAF0] text-sm">
          <p className="font-mono text-[#E8EAF0] mb-2">{label}</p>
          <div className="flex justify-between gap-6 mt-1">
            <span className="text-[#9BA1B0]">Baseline:</span>
            <span className="font-mono text-[#9BA1B0]">{data.baseline.toFixed(2)}</span>
          </div>
          <div className="flex justify-between gap-6 mt-1">
            <span className="text-[#9BA1B0]">Simulated:</span>
            <span className="font-mono text-[#22D3EE]">{data.simulated.toFixed(2)}</span>
          </div>
          <div className="flex justify-between gap-6 mt-2 pt-2 border-t border-[#22252F]">
            <span className="text-[#9BA1B0]">Impact:</span>
            <span className="font-mono" style={{ color: data.change_pct >= 0 ? '#34D399' : '#F43F5E' }}>
              {data.change_pct > 0 ? '+' : ''}{(data.change_pct * 100).toFixed(2)}%
            </span>
          </div>
        </div>
      );
    }
    return null;
  };

  const CustomLabel = (props: any) => {
    const { x, y, width, payload } = props;
    if (!payload) return null;
    const isPositive = payload.change_pct >= 0;
    const color = isPositive ? '#34D399' : '#F43F5E';
    const text = `${isPositive ? '+' : ''}${(payload.change_pct * 100).toFixed(1)}%`;
    
    return (
      <text 
        x={x + width / 2} 
        y={y - 8} 
        fill={color} 
        fontSize={10} 
        fontFamily="monospace"
        textAnchor="middle"
      >
        {text}
      </text>
    );
  };

  return (
    <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4">
      <h3 className="label-mono text-[#E8EAF0] text-sm uppercase tracking-wider font-semibold mb-4">Simulation Impact</h3>
      
      <div className="w-full h-[400px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={segments} margin={{ top: 20, right: 10, bottom: 20, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#22252F" vertical={false} />
            <XAxis 
              dataKey="segment" 
              stroke="#9BA1B0" 
              fontSize={12} 
              tickLine={false} 
              axisLine={false}
              tickMargin={10}
            />
            <YAxis 
              stroke="#9BA1B0" 
              fontSize={12} 
              tickLine={false} 
              axisLine={false}
              tickMargin={10}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: '#22252F', opacity: 0.4 }} />
            <Legend wrapperStyle={{ fontSize: '12px', color: '#9BA1B0' }} />
            
            <Bar dataKey="baseline" name="Baseline" fill="#5D6474" radius={[4, 4, 0, 0]} />
            <Bar dataKey="simulated" name="Simulated" fill="#22D3EE" radius={[4, 4, 0, 0]}>
              {/* @ts-ignore */}
              <CustomLabel position="top" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
