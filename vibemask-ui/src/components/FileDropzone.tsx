import React, { useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import { UploadCloud, File as FileIcon } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface FileDropzoneProps {
    onFileSelect: (file: File) => void;
    selectedFile: File | null;
    label?: string;
}

export const FileDropzone: React.FC<FileDropzoneProps> = ({ onFileSelect, selectedFile, label = "Drag & drop files here" }) => {
    const onDrop = useCallback((acceptedFiles: File[]) => {
        if (acceptedFiles.length > 0) {
            onFileSelect(acceptedFiles[0]);
        }
    }, [onFileSelect]);

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop,
        multiple: false
    });

    return (
        <div
            {...getRootProps()}
            className={`
        p-10 border-2 border-dashed rounded-xl cursor-pointer transition-all duration-300
        flex flex-col items-center justify-center text-center group
        ${isDragActive
                    ? 'border-primary bg-primary/10 scale-[1.02]'
                    : 'border-muted hover:border-primary/50 hover:bg-muted/30'
                }
      `}
        >
            <input {...getInputProps()} />

            <AnimatePresence mode="wait">
                {selectedFile ? (
                    <motion.div
                        key="file"
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -10 }}
                        className="flex flex-col items-center gap-2"
                    >
                        <div className="p-4 bg-primary/10 rounded-full text-primary">
                            <FileIcon size={32} />
                        </div>
                        <div className="mt-2">
                            <p className="font-medium text-lg">{selectedFile.name}</p>
                            <p className="text-sm text-gray-500">{(selectedFile.size / 1024).toFixed(1)} KB</p>
                        </div>
                        <p className="text-xs text-primary mt-2 group-hover:underline">Click or drop to replace</p>
                    </motion.div>
                ) : (
                    <motion.div
                        key="empty"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="flex flex-col items-center gap-2"
                    >
                        <div className={`p-4 rounded-full transition-colors ${isDragActive ? 'bg-primary text-white' : 'bg-muted text-gray-400 group-hover:text-primary'}`}>
                            <UploadCloud size={32} />
                        </div>
                        <div className="mt-2">
                            <p className="font-medium text-lg text-gray-700 dark:text-gray-200">
                                {isDragActive ? "Drop it!" : label}
                            </p>
                            <p className="text-sm text-gray-500 mt-1">
                                Supports .docx, .xlsx, .pdf, .txt, .md
                            </p>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};
