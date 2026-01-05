import { useState, useEffect } from 'react';
import { Command } from '@tauri-apps/plugin-shell';
import { FileDropzone } from './components/FileDropzone';
import { AnalysisTable } from './components/AnalysisTable';
import { maskFile, restoreFile, getDownloadUrl } from './api';
import type { MaskResponse, RestoreResponse } from './api';
import { Shield, RefreshCw, Download, Loader2, CheckCircle2 } from 'lucide-react';

function App() {
  const [activeTab, setActiveTab] = useState<'mask' | 'restore'>('mask');
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);

  // Mask State
  const [maskResult, setMaskResult] = useState<MaskResponse | null>(null);

  // Restore State
  const [restoreResult, setRestoreResult] = useState<RestoreResponse | null>(null);

  // Sidecar handling - only in Tauri environment
  useEffect(() => {
    const startSidecar = async () => {
      // Check if running in Tauri webview
      if (!(window as any).__TAURI__) {
        console.log("Not in Tauri environment, skipping sidecar spawn");
        return;
      }
      try {
        console.log("Attempting to spawn sidecar...");
        // Command name matches the externalBin entry in tauri.conf.json (minus extension/triple)
        const command = Command.sidecar('binaries/vibemask-server');
        const child = await command.spawn();
        console.log("Sidecar spawned with PID:", child.pid);
      } catch (e) {
        console.error("Sidecar spawn failed:", e);
      }
    };
    startSidecar();
  }, []);

  const handleMask = async () => {
    if (!file) return;
    setLoading(true);
    console.log("Starting masking for file:", file.name);
    try {
      const result = await maskFile(file);
      console.log("Mask result:", result);
      setMaskResult(result);
    } catch (e) {
      console.error("Mask error:", e);
      alert("Error processing file: " + e);
    } finally {
      setLoading(false);
    }
  };

  const handleRestore = async () => {
    if (!file) return;
    setLoading(true);
    try {
      const result = await restoreFile(file);
      setRestoreResult(result);
    } catch (e) {
      alert("Error restoring file: " + e);
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setFile(null);
    setMaskResult(null);
    setRestoreResult(null);
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100 flex flex-col items-center py-12 px-4">
      <div className="max-w-4xl w-full">
        <header className="mb-10 text-center">
          <div className="inline-flex items-center gap-2 mb-2 p-2 bg-white dark:bg-gray-900 rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800">
            <div className="w-10 h-10 bg-primary rounded-xl flex items-center justify-center text-white">
              <Shield size={24} />
            </div>
            <h1 className="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-primary to-purple-600 px-2">VibeMask</h1>
          </div>
          <p className="text-gray-500">Secure, local, and lossless PII masking & restoration</p>
        </header>

        {/* Tabs */}
        <div className="flex p-1 bg-white dark:bg-gray-900 rounded-xl shadow-sm border border-gray-200 dark:border-gray-800 mb-8 mx-auto w-fit">
          <button
            onClick={() => { setActiveTab('mask'); reset(); }}
            className={`px-6 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'mask'
              ? 'bg-primary text-white shadow-md'
              : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
          >
            Mask Data
          </button>
          <button
            onClick={() => { setActiveTab('restore'); reset(); }}
            className={`px-6 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'restore'
              ? 'bg-active text-white shadow-md'
              : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
          >
            Restore Data
          </button>
        </div>

        <main className="bg-white dark:bg-gray-900 p-8 rounded-2xl shadow-xl border border-gray-100 dark:border-gray-800 relative overflow-hidden">

          {/* Main Content */}
          <div className="relative z-10">
            {!maskResult && !restoreResult && (
              <>
                <FileDropzone
                  selectedFile={file}
                  onFileSelect={setFile}
                  label={activeTab === 'mask' ? "Drop file to mask" : "Drop masked file to restore"}
                />

                {file && (
                  <div className="mt-8 flex justify-center">
                    <button
                      onClick={activeTab === 'mask' ? handleMask : handleRestore}
                      disabled={loading}
                      className={`
                        flex items-center gap-2 px-8 py-3 rounded-xl font-bold text-white shadow-lg shadow-primary/20 hover:scale-105 active:scale-95 transition-all
                        ${loading ? 'opacity-70 cursor-not-allowed' : ''}
                        ${activeTab === 'mask' ? 'bg-primary' : 'bg-active'}
                      `}
                    >
                      {loading ? <Loader2 className="animate-spin" /> : activeTab === 'mask' ? <Shield size={20} /> : <RefreshCw size={20} />}
                      {loading ? 'Processing...' : activeTab === 'mask' ? 'Start Masking' : 'Restore Original'}
                    </button>
                  </div>
                )}
              </>
            )}

            {/* Mask Success View */}
            {maskResult && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-green-100 text-green-600 rounded-full">
                      <CheckCircle2 size={24} />
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-gray-900 dark:text-white">Masking Complete!</h2>
                      <p className="text-sm text-gray-500">Session ID: <span className="font-mono bg-gray-100 dark:bg-gray-800 px-1 rounded">{maskResult.session_id}</span></p>
                    </div>
                  </div>
                  <a
                    href={getDownloadUrl(maskResult.file_path)}
                    className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors shadow-md"
                    target="_blank"
                    rel="noreferrer"
                  >
                    <Download size={18} />
                    Download Masked File
                  </a>
                </div>

                <AnalysisTable data={maskResult.preview} />

                <button onClick={reset} className="mt-8 text-sm text-gray-400 hover:text-gray-900 underline">
                  Process another file
                </button>
              </div>
            )}

            {/* Restore Success View */}
            {restoreResult && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 text-center py-10">
                <div className="inline-flex p-4 bg-green-100 text-green-600 rounded-full mb-4">
                  <CheckCircle2 size={48} />
                </div>
                <h2 className="text-2xl font-bold mb-2">Restoration Successful!</h2>
                <p className="text-gray-500 mb-8">
                  Restored <span className="font-bold text-gray-900 dark:text-white">{restoreResult.restored_count}</span> entities
                  using session <span className="font-mono bg-gray-100 dark:bg-gray-800 px-1 rounded">{restoreResult.session_id}</span>
                </p>

                <a
                  href={getDownloadUrl(restoreResult.file_path)}
                  className="inline-flex items-center gap-2 px-8 py-3 bg-active text-white rounded-xl hover:bg-active/90 transition-colors shadow-lg shadow-green-500/20 text-lg font-bold"
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download size={24} />
                  Download Restored File
                </a>

                <div className="mt-8">
                  <button onClick={reset} className="text-sm text-gray-400 hover:text-gray-900 underline">
                    Process another file
                  </button>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

export default App;
