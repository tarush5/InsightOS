"use client";

import React, { useMemo } from 'react';
import {
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine
} from 'recharts';

export interface DistributionChartProps {
  data: number[];
  label?: string;
  bins?: number;
}

export const DistributionChart: React.FC<DistributionChartProps> = ({ data, label = 'Distribution', bins = 20 }) => {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return { binsData: [], stats: null };

    const min = Math.min(...data);
    const max = Math.max(...data);
    const mean = data.reduce((a, b) => a + b, 0) / data.length;
    
    const sorted = [...data].sort((a, b) => a - b);
    const median = sorted.length % 2 === 0 
      ? (sorted[sorted.length / 2 - 1]! + sorted[sorted.length / 2]!) / 2
      : sorted[Math.floor(sorted.length / 2)]!;

    const squareDiffs = data.map(val => Math.pow(val - mean, 2));
    const variance = squareDiffs.reduce((a, b) => a + b, 0) / data.length;
    const std = Math.sqrt(variance);

    const n = data.length;
    let sumCubedDiffs = 0;
    for (let i = 0; i < n; i++) {
        sumCubedDiffs += Math.pow(data[i]! - mean, 3);
    }
    const skewness = (n > 2 && std > 0) ? (sumCubedDiffs / n) / Math.pow(std, 3) : 0;

    const binSize = (max - min) / bins;
    const binsData = Array.from({ length: bins }, (_, i) => {
      const binMin = min + i * binSize;
      const binMax = binMin + binSize;
      return {
        binMin,
        binMax,
        midPoint: binMin + binSize / 2,
        count: 0,
        density: 0,
      };
    });

    data.forEach(val => {
      let index = Math.floor((val - min) / binSize);
      if (index === bins) index--;
      if (index >= 0 && index < bins && binsData[index]) {
        binsData[index]!.count++;
      }
    });

    const bandwidth = 1.06 * std * Math.pow(n, -0.2);
    const kernel = (x: number) => (1 / Math.sqrt(2 * Math.PI)) * Math.exp(-0.5 * x * x);
    
    if (bandwidth > 0) {
      binsData.forEach(bin => {
        let densitySum = 0;
        data.forEach(val => {
          densitySum += kernel((bin.midPoint - val) / bandwidth);
        });
        bin.density = (densitySum / (n * bandwidth)) * (data.length * binSize); 
      });
    }

    return {
      binsData,
      stats: { count: data.length, mean, median, std, min, max, skewness }
    };
  }, [data, bins]);

  if (!data || data.length === 0) {
    return (
      <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4 min-h-[300px] flex items-center justify-center">
        <span className="text-[#9BA1B0] font-mono text-sm">No data available</span>
      </div>
    );
  }

  const { binsData, stats } = chartData;

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const pData = payload[0].payload;
      return (
        <div className="bg-[#161923] border border-[#22252F] p-3 rounded-md shadow-lg text-[#E8EAF0] text-sm">
          <p className="font-mono text-[#9BA1B0] mb-2">
            Range: {pData.binMin.toFixed(2)} - {pData.binMax.toFixed(2)}
          </p>
          <div className="flex justify-between gap-4 mt-1">
            <span className="text-[#9BA1B0]">Count:</span>
            <span className="font-mono text-[#22D3EE]">{pData.count}</span>
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4">
      <h3 className="label-mono text-[#E8EAF0] text-sm uppercase tracking-wider font-semibold mb-4">{label}</h3>
      
      <div className="w-full h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={binsData} margin={{ top: 10, right: 10, bottom: 10, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#22252F" vertical={false} />
            <XAxis 
              dataKey="midPoint" 
              stroke="#9BA1B0" 
              fontSize={12} 
              tickFormatter={(val) => val.toFixed(1)}
              tickLine={false} 
              axisLine={false}
              tickMargin={10}
            />
            <YAxis 
              yAxisId="left"
              stroke="#9BA1B0" 
              fontSize={12} 
              tickLine={false} 
              axisLine={false}
              tickMargin={10}
            />
            <YAxis 
              yAxisId="right"
              orientation="right"
              hide={true}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: '#22252F', opacity: 0.4 }} />
            
            <Bar yAxisId="left" dataKey="count" fill="#22D3EE" opacity={0.3} radius={[2, 2, 0, 0]} />
            <Line yAxisId="left" type="monotone" dataKey="density" stroke="#8B5CF6" dot={false} strokeWidth={2} />
            
            {stats && (
              <>
                <ReferenceLine yAxisId="left" x={stats.mean} stroke="#22D3EE" strokeDasharray="3 3" label={{ position: 'top', value: 'Mean', fill: '#22D3EE', fontSize: 10 }} />
                <ReferenceLine yAxisId="left" x={stats.median} stroke="#8B5CF6" strokeDasharray="3 3" label={{ position: 'insideTopLeft', value: 'Median', fill: '#8B5CF6', fontSize: 10 }} />
              </>
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {stats && (
        <div className="grid grid-cols-3 md:grid-cols-6 gap-4 mt-6 pt-4 border-t border-[#22252F]">
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">Count</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{stats.count}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">Mean</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{stats.mean.toFixed(2)}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">Std Dev</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{stats.std.toFixed(2)}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">Min</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{stats.min.toFixed(2)}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">Max</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{stats.max.toFixed(2)}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">Skewness</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{stats.skewness.toFixed(2)}</span>
          </div>
        </div>
      )}
    </div>
  );
};
