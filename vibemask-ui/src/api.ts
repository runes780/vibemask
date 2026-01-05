import axios from 'axios';

const api = axios.create({
    baseURL: 'http://localhost:8000',
});

export interface AnalysisResult {
    original: string;
    type: string;
    masked: string;
    count: number;
}

export interface MaskResponse {
    file_path: string;
    session_id: string;
    stats: Record<string, number>;
    preview: AnalysisResult[];
}

export interface RestoreResponse {
    file_path: string;
    session_id: string;
    restored_count: number;
}

export const analyzeFile = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post<AnalysisResult[]>('/analyze', formData);
    return response.data;
};

export const maskFile = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post<MaskResponse>('/mask', formData);
    return response.data;
};

export const restoreFile = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await api.post<RestoreResponse>('/restore', formData);
    return response.data;
};

export const getDownloadUrl = (filename: string) => {
    return `http://localhost:8000/download/${filename}`;
};
