"use client";

import React, { useState } from 'react';
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import { TrendingUp, TrendingDown, ChevronDown, ChevronRight, Activity, Zap } from 'lucide-react';

export interface ForecastChartProps {
  forecast: {
    dates: string[];
    predicted: number[];
    lower_80: number[];
    upper_80: number[];
    history_dates: string[];
    history_values: number[];
    model: string;
    trend_direction: string;
    seasonality_detected: boolean;
    caveats: string[];
    metrics: {
      mae: number;
      rmse: number;
      mape: number | null;
      mase: number;
      beats_baseline: boolean;
    };
  };
}

export const ForecastChart: React.FC<ForecastChartProps> = ({ forecast }) => {
  const [showCaveats, setShowCaveats] = useState(false);

  const {
    dates,
    predicted,
    lower_80,
    upper_80,
    history_dates,
    history_values,
    trend_direction,
    seasonality_detected,
    caveats,
    metrics,
  } = forecast;

  const combinedData: any[] = [];
  
  history_dates.forEach((date, i) => {
    combinedData.push({
      date,
      actual: history_values[i],
      predicted: null,
      bounds: null,
    });
  });

  const lastHistoryDate = history_dates.length > 0 ? history_dates[history_dates.length - 1] : null;

  dates.forEach((date, i) => {
    combinedData.push({
      date,
      actual: null,
      predicted: predicted[i],
      bounds: [lower_80[i], upper_80[i]],
    });
  });

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-[#161923] border border-[#22252F] p-3 rounded-md shadow-lg text-[#E8EAF0] text-sm">
          <p className="font-mono text-[#9BA1B0] mb-2">{label}</p>
          {payload.map((entry: any, index: number) => {
            if (entry.dataKey === 'bounds' && entry.value) {
              return (
                <div key={index} className="flex justify-between gap-4 mt-1">
                  <span className="text-[#9BA1B0]">80% Interval:</span>
                  <span className="font-mono text-[#22D3EE]">{entry.value[0].toFixed(2)} - {entry.value[1].toFixed(2)}</span>
                </div>
              );
            }
            if (entry.dataKey === 'actual' && entry.value != null) {
              return (
                <div key={index} className="flex justify-between gap-4 mt-1">
                  <span className="text-[#9BA1B0]">Actual:</span>
                  <span className="font-mono">{entry.value.toFixed(2)}</span>
                </div>
              );
            }
            if (entry.dataKey === 'predicted' && entry.value != null) {
              return (
                <div key={index} className="flex justify-between gap-4 mt-1">
                  <span className="text-[#9BA1B0]">Predicted:</span>
                  <span className="font-mono text-[#22D3EE]">{entry.value.toFixed(2)}</span>
                </div>
              );
            }
            return null;
          })}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="panel w-full flex flex-col gap-4 bg-[#101218] border border-[#22252F] rounded-lg p-4">
      <div className="flex justify-between items-center">
        <div className="flex items-center gap-3">
          <h3 className="label-mono text-[#E8EAF0] text-sm uppercase tracking-wider font-semibold">Forecast</h3>
          {trend_direction === 'up' && <TrendingUp className="w-4 h-4 text-[#34D399]" />}
          {trend_direction === 'down' && <TrendingDown className="w-4 h-4 text-[#F43F5E]" />}
          {seasonality_detected && <span title="Seasonality Detected"><Activity className="w-4 h-4 text-[#8B5CF6]" /></span>}
        </div>
      </div>

      <div className="w-full h-[500px]">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={combinedData} margin={{ top: 20, right: 20, bottom: 20, left: 0 }}>
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
            <Tooltip content={<CustomTooltip />} />
            
            {lastHistoryDate && (
              <ReferenceLine x={lastHistoryDate} stroke="#9BA1B0" strokeDasharray="3 3" />
            )}

            <Area 
              type="monotone" 
              dataKey="bounds" 
              stroke="none" 
              fill="#22D3EE" 
              fillOpacity={0.1} 
              isAnimationActive={false}
            />
            <Line 
              type="monotone" 
              dataKey="actual" 
              stroke="#9BA1B0" 
              strokeDasharray="4 4" 
              dot={false} 
              strokeWidth={2}
            />
            <Line 
              type="monotone" 
              dataKey="predicted" 
              stroke="#22D3EE" 
              dot={false} 
              strokeWidth={2}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="flex flex-col gap-3 mt-2 pt-4 border-t border-[#22252F]">
        <div className="flex gap-4 items-center flex-wrap">
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">MAE</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{metrics.mae.toFixed(2)}</span>
          </div>
          <div className="flex flex-col">
            <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">RMSE</span>
            <span className="font-mono text-[#E8EAF0] text-sm">{metrics.rmse.toFixed(2)}</span>
          </div>
          {metrics.mape !== null && (
            <div className="flex flex-col">
              <span className="text-[#9BA1B0] text-xs uppercase tracking-wider">MAPE</span>
              <span className="font-mono text-[#E8EAF0] text-sm">{(metrics.mape * 100).toFixed(2)}%</span>
            </div>
          )}
          {metrics.beats_baseline && (
            <div className="ml-auto flex items-center gap-1 text-[#34D399] bg-[#34D399]/10 px-2 py-1 rounded text-xs font-medium">
              <Zap className="w-3 h-3" /> Beats Baseline
            </div>
          )}
        </div>

        {caveats && caveats.length > 0 && (
          <div className="mt-2">
            <button 
              onClick={() => setShowCaveats(!showCaveats)}
              className="flex items-center gap-1 text-xs text-[#9BA1B0] hover:text-[#E8EAF0] transition-colors"
            >
              {showCaveats ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
              <span>Model Caveats ({caveats.length})</span>
            </button>
            {showCaveats && (
              <ul className="mt-2 space-y-1 text-xs text-[#9BA1B0] bg-[#161923] p-3 rounded border border-[#22252F]">
                {caveats.map((c, i) => (
                  <li key={i} className="list-disc ml-4">{c}</li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
