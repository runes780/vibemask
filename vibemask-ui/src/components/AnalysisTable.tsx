import React from 'react';
import type { AnalysisResult } from '../api';

interface AnalysisTableProps {
    data: AnalysisResult[];
}

export const AnalysisTable: React.FC<AnalysisTableProps> = ({ data }) => {
    if (!data || data.length === 0) return null;

    return (
        <div className="w-full overflow-hidden rounded-lg border border-gray-200 dark:border-gray-800 shadow-sm mt-6">
            <div className="max-h-[400px] overflow-y-auto">
                <table className="w-full text-sm text-left">
                    <thead className="text-xs text-gray-500 uppercase bg-gray-50 dark:bg-gray-900 border-b dark:border-gray-800 sticky top-0 z-10 backdrop-blur-sm">
                        <tr>
                            <th className="px-6 py-3">Type</th>
                            <th className="px-6 py-3">Original</th>
                            <th className="px-6 py-3">Masked</th>
                            <th className="px-6 py-3 text-right">Count</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.map((row, index) => (
                            <tr
                                key={index}
                                className="bg-white dark:bg-gray-950 border-b dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
                            >
                                <td className="px-6 py-4 font-medium">
                                    <span className="px-2 py-1 rounded-md text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                                        {row.type}
                                    </span>
                                </td>
                                <td className="px-6 py-4 text-red-600 font-mono dark:text-red-400">{row.original}</td>
                                <td className="px-6 py-4 text-green-600 font-mono dark:text-green-400">{row.masked}</td>
                                <td className="px-6 py-4 text-right text-gray-500">{row.count}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <div className="px-6 py-3 bg-gray-50 dark:bg-gray-900 border-t dark:border-gray-800 text-xs text-gray-500 text-right">
                Total entities: {data.length}
            </div>
        </div>
    );
};
