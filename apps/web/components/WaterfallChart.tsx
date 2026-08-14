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
  Cell,
} from 'recharts';

export interface WaterfallChartProps {
  drivers: {
    dimension: string;
    segment: string;
    absolute_change: number;
    contribution_pct: number;
    share_of_change: number;
    status: string;
  }[];
}

export const WaterfallChart: React.FC<WaterfallChartProps> = ({ drivers }) => {
  if (!drivers || drivers.length === 0) {
    return (
      <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4 min-h-[300px] flex items-center justify-center">
        <span className="text-[#9BA1B0] font-mono text-sm">No drivers data available</span>
      </div>
    );
  }

  const sortedDrivers = [...drivers].sort((a, b) => Math.abs(b.absolute_change) - Math.abs(a.absolute_change)).slice(0, 10);
  
  const totalChange = drivers.reduce((acc, d) => acc + d.absolute_change, 0);

  const data = [
    {
      name: 'Total Change',
      value: totalChange,
      isTotal: true,
      raw: null,
    },
    ...sortedDrivers.map(d => ({
      name: d.segment,
      value: d.absolute_change,
      isTotal: false,
      raw: d,
    })),
  ];

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="bg-[#161923] border border-[#22252F] p-3 rounded-md shadow-lg text-[#E8EAF0] text-sm">
          <p className="font-mono text-[#E8EAF0] mb-2">{data.name}</p>
          <div className="flex justify-between gap-6 mt-1">
            <span className="text-[#9BA1B0]">Absolute Change:</span>
            <span className="font-mono" style={{ color: data.value >= 0 ? '#34D399' : '#F43F5E' }}>
              {data.value > 0 ? '+' : ''}{data.value.toFixed(2)}
            </span>
          </div>
          {!data.isTotal && data.raw && (
            <div className="flex justify-between gap-6 mt-1">
              <span className="text-[#9BA1B0]">Contribution:</span>
              <span className="font-mono">{(data.raw.contribution_pct * 100).toFixed(1)}%</span>
            </div>
          )}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4">
      <h3 className="label-mono text-[#E8EAF0] text-sm uppercase tracking-wider font-semibold mb-4">Driver Contributions</h3>
      <div className="w-full h-[400px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#22252F" horizontal={false} />
            <XAxis type="number" stroke="#9BA1B0" fontSize={12} tickLine={false} axisLine={false} />
            <YAxis 
              type="category" 
              dataKey="name" 
              stroke="#9BA1B0" 
              fontSize={12} 
              tickLine={false} 
              axisLine={false} 
              width={120} 
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: '#22252F', opacity: 0.4 }} />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {data.map((entry, index) => (
                <Cell 
                  key={`cell-${index}`} 
                  fill={entry.isTotal ? '#22D3EE' : entry.value >= 0 ? '#34D399' : '#F43F5E'} 
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
