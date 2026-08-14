"use client";

import React, { useMemo } from 'react';
import {
  ComposedChart,
  Area,
  Line,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer
} from 'recharts';
import { AlertCircle } from 'lucide-react';

export interface AnomalyTimelineProps {
  dates: string[];
  values: number[];
  anomalies?: {
    date: string;
    value: number;
    severity: 'low' | 'medium' | 'high';
    reason?: string;
  }[];
  upper_bound?: number[];
  lower_bound?: number[];
}

export const AnomalyTimeline: React.FC<AnomalyTimelineProps> = ({
  dates,
  values,
  anomalies = [],
  upper_bound,
  lower_bound
}) => {
  const chartData = useMemo(() => {
    return dates.map((date, i) => {
      const anomaly = anomalies.find(a => a.date === date);
      return {
        date,
        value: values[i],
        bounds: upper_bound && lower_bound ? [lower_bound[i], upper_bound[i]] : null,
        anomalyValue: anomaly ? anomaly.value : null,
        anomalyData: anomaly || null,
      };
    });
  }, [dates, values, anomalies, upper_bound, lower_bound]);

  if (!dates || dates.length === 0) {
    return (
      <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4 min-h-[300px] flex items-center justify-center">
        <span className="text-[#9BA1B0] font-mono text-sm">No timeline data available</span>
      </div>
    );
  }

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div className="bg-[#161923] border border-[#22252F] p-3 rounded-md shadow-lg text-[#E8EAF0] text-sm max-w-[250px]">
          <p className="font-mono text-[#9BA1B0] mb-2">{label}</p>
          <div className="flex justify-between gap-4 mt-1">
            <span className="text-[#9BA1B0]">Value:</span>
            <span className="font-mono text-[#22D3EE]">{data.value?.toFixed(2)}</span>
          </div>
          {data.bounds && (
            <div className="flex justify-between gap-4 mt-1">
              <span className="text-[#9BA1B0]">Expected:</span>
              <span className="font-mono text-[#9BA1B0]">{data.bounds[0].toFixed(2)} - {data.bounds[1].toFixed(2)}</span>
            </div>
          )}
          {data.anomalyData && (
            <div className="mt-3 pt-2 border-t border-[#22252F]">
              <div className="flex items-center gap-1 mb-1" style={{ color: data.anomalyData.severity === 'low' ? '#F59E0B' : '#F43F5E' }}>
                <AlertCircle className="w-3 h-3" />
                <span className="uppercase text-[10px] font-bold tracking-wider">
                  {data.anomalyData.severity} Severity Anomaly
                </span>
              </div>
              {data.anomalyData.reason && (
                <p className="text-xs text-[#E8EAF0] mt-1 break-words">{data.anomalyData.reason}</p>
              )}
            </div>
          )}
        </div>
      );
    }
    return null;
  };

  const hasBounds = upper_bound && lower_bound;

  return (
    <div className="panel w-full bg-[#101218] border border-[#22252F] rounded-lg p-4">
      <h3 className="label-mono text-[#E8EAF0] text-sm uppercase tracking-wider font-semibold mb-4">Anomaly Timeline</h3>
      
      <div className="w-full h-[350px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#22252F" vertical={false} />
            <XAxis 
              dataKey="date" 
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
            <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#22252F', strokeWidth: 1, strokeDasharray: '4 4' }} />
            
            {hasBounds && (
              <Area 
                type="monotone" 
                dataKey="bounds" 
                stroke="none" 
                fill="#22252F" 
                fillOpacity={0.5} 
                isAnimationActive={false}
              />
            )}
            
            <Line 
              type="monotone" 
              dataKey="value" 
              stroke="#22D3EE" 
              dot={false} 
              strokeWidth={2}
              isAnimationActive={false}
            />
            
            <Scatter 
              dataKey="anomalyValue" 
              isAnimationActive={false}
              shape={(props: any) => {
                const { cx, cy, payload } = props;
                if (!payload.anomalyData) return <circle cx={0} cy={0} r={0} fill="none" />;
                const fill = payload.anomalyData.severity === 'low' ? '#F59E0B' : '#F43F5E';
                return (
                  <circle cx={cx} cy={cy} r={5} fill={fill} stroke="#101218" strokeWidth={2} />
                );
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
