"use client";

import React, { useMemo } from 'react';
import {
  LineChart,
  Line,
  ResponsiveContainer,
  YAxis
} from 'recharts';

export interface KPISparklineProps {
  values: number[];
  color?: string;
  height?: number;
}

export const KPISparkline: React.FC<KPISparklineProps> = ({ values, color, height = 32 }) => {
  const data = useMemo(() => values.map((val, i) => ({ index: i, value: val })), [values]);

  if (!values || values.length < 2) {
    return <div style={{ height, width: '100%' }} className="bg-[#161923] rounded opacity-50" />;
  }

  const isPositive = values[values.length - 1]! >= values[0]!;
  const defaultColor = isPositive ? '#34D399' : '#F43F5E';
  const strokeColor = color || defaultColor;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const domain = [min - (max - min) * 0.1, max + (max - min) * 0.1];

  return (
    <div style={{ height, width: '100%' }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <defs>
            <linearGradient id={`colorGradient-${strokeColor}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={strokeColor} stopOpacity={0.3} />
              <stop offset="95%" stopColor={strokeColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis domain={domain} hide={true} />
          <Line 
            type="monotone" 
            dataKey="value" 
            stroke={strokeColor} 
            strokeWidth={2} 
            dot={false} 
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
