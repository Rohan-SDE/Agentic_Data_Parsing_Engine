import { useEffect, useRef, useState } from "react";
import axios from "axios";
import ReactMarkdown from "react-markdown";
import "./App.css";

const API_BASE_URL = "http://127.0.0.1:8000";

function App() {
  // ============================================================
  // FILE / PROCESSING STATE
  // ============================================================

  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  // Phase 9 async processing
  const [jobId, setJobId] = useState("");
  const [jobStatus, setJobStatus] = useState(null);
  const [progress, setProgress] = useState(0);

  // ============================================================
  // DASHBOARD STATE
  // ============================================================

  const [reports, setReports] = useState([]);
  const [processedFiles, setProcessedFiles] = useState([]);
  const [rejectedFiles, setRejectedFiles] = useState([]);

  // ============================================================
  // REPORT VIEWER STATE
  // ============================================================

  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerType, setViewerType] = useState("");
  const [viewerTitle, setViewerTitle] = useState("");
  const [viewerContent, setViewerContent] = useState("");
  const [viewerLoading, setViewerLoading] = useState(false);
  const [viewerUrl, setViewerUrl] = useState("");

  // Prevent polling from creating multiple timers
  const pollingRef = useRef(null);

  // ============================================================
  // LOAD DASHBOARD DATA
  // ============================================================

  const fetchDashboardData = async () => {
    try {
      const [
        reportsResponse,
        processedResponse,
        rejectedResponse,
      ] = await Promise.all([
        axios.get(`${API_BASE_URL}/reports`),
        axios.get(`${API_BASE_URL}/processed`),
        axios.get(`${API_BASE_URL}/rejected`),
      ]);

      setReports(reportsResponse.data.files || []);
      setProcessedFiles(processedResponse.data.files || []);
      setRejectedFiles(rejectedResponse.data.files || []);
    } catch (error) {
      console.error(
        "Failed to load dashboard data:",
        error
      );
    }
  };

  // ============================================================
  // INITIAL LOAD
  // ============================================================

  useEffect(() => {
    fetchDashboardData();

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    };
  }, []);

  // ============================================================
  // FILE SELECTION
  // ============================================================

  const handleFileChange = (event) => {
    const selectedFile = event.target.files[0];

    if (!selectedFile) {
      return;
    }

    // Only allow CSV
    if (!selectedFile.name.toLowerCase().endsWith(".csv")) {
      setFile(null);
      setStatus("Please select a CSV file.");
      return;
    }

    setFile(selectedFile);
    setStatus("");
    setResult(null);
    setJobId("");
    setJobStatus(null);
    setProgress(0);
  };

  // ============================================================
  // START ASYNC UPLOAD
  // ============================================================

  const handleUpload = async () => {
    if (!file) {
      setStatus("Please select a CSV file first.");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    try {
      // ----------------------------------------
      // Reset previous job
      // ----------------------------------------

      setLoading(true);
      setStatus("Uploading file...");
      setResult(null);
      setJobId("");
      setJobStatus(null);
      setProgress(0);

      // ----------------------------------------
      // Upload asynchronously
      // ----------------------------------------

      const response = await axios.post(
        `${API_BASE_URL}/upload-async`,
        formData
      );

      const returnedJobId = response.data.job_id;

      if (!returnedJobId) {
        throw new Error(
          "Backend did not return a job ID."
        );
      }

      // ----------------------------------------
      // Save job information
      // ----------------------------------------

      setJobId(returnedJobId);

      setJobStatus({
        status: response.data.status || "queued",
        message:
          response.data.message ||
          "Processing started.",
      });

      setProgress(5);

      setStatus(
        "File uploaded. Processing started..."
      );

      // ----------------------------------------
      // Start polling
      // ----------------------------------------

      startJobPolling(returnedJobId);

    } catch (error) {
      console.error(
        "Async upload failed:",
        error
      );

      setLoading(false);

      setStatus(
        error.response?.data?.detail ||
          error.message ||
          "Failed to upload the file. Make sure the backend is running."
      );
    }
  };

  // ============================================================
  // POLL JOB STATUS
  // ============================================================

  const startJobPolling = (id) => {
    // Clear an existing polling timer
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
    }

    // Immediately check once
    checkJobStatus(id);

    // Then check every 2 seconds
    pollingRef.current = setInterval(() => {
      checkJobStatus(id);
    }, 2000);
  };

  // ============================================================
  // CHECK JOB STATUS
  // ============================================================

  const checkJobStatus = async (id) => {
    try {
      const response = await axios.get(
        `${API_BASE_URL}/jobs/${id}`
      );

      const job = response.data;

      setJobStatus(job);

      // ----------------------------------------
      // Progress
      // ----------------------------------------

      if (
        typeof job.progress === "number"
      ) {
        setProgress(job.progress);
      }

      // ----------------------------------------
      // QUEUED
      // ----------------------------------------

      if (
        job.status === "queued"
      ) {
        setStatus(
          job.message ||
            "Job is waiting to start..."
        );

        return;
      }

      // ----------------------------------------
      // PROCESSING
      // ----------------------------------------

      if (
        job.status === "processing"
      ) {
        setStatus(
          job.message ||
            "Processing file..."
        );

        return;
      }

      // ----------------------------------------
      // COMPLETED
      // ----------------------------------------

      if (
        job.status === "completed"
      ) {
        handleJobCompleted(job);
        return;
      }

      // ----------------------------------------
      // FAILED
      // ----------------------------------------

      if (
        job.status === "failed"
      ) {
        handleJobFailed(job);
        return;
      }

    } catch (error) {
      console.error(
        "Failed to check job status:",
        error
      );

      setStatus(
        "Unable to check processing status. Retrying..."
      );
    }
  };

  // ============================================================
  // JOB COMPLETED
  // ============================================================

  const handleJobCompleted = async (job) => {
    // Stop polling
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    setLoading(false);
    setProgress(100);

    setStatus(
      job.message ||
        "Processing completed successfully."
    );

    // ----------------------------------------
    // Backend result
    // ----------------------------------------

    if (job.result) {
      setResult(job.result);
    } else {
      // Some job manager implementations may
      // return result fields directly.
      setResult(job);
    }

    // ----------------------------------------
    // Refresh dashboard
    // ----------------------------------------

    await fetchDashboardData();
  };

  // ============================================================
  // JOB FAILED
  // ============================================================

  const handleJobFailed = (job) => {
    // Stop polling
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    setLoading(false);

    setStatus(
      job.error ||
        job.message ||
        "File processing failed."
    );
  };

  // ============================================================
  // GET FILENAME
  // ============================================================

  const getFilename = (path) => {
    if (!path) {
      return "";
    }

    return path
      .split("\\")
      .pop()
      .split("/")
      .pop();
  };

  // ============================================================
  // REPORT URL
  // ============================================================

  const getReportUrl = (pathOrFilename) => {
    const filename =
      getFilename(pathOrFilename);

    return `${API_BASE_URL}/reports/${encodeURIComponent(
      filename
    )}`;
  };

  // ============================================================
  // FILE SIZE
  // ============================================================

  const formatFileSize = (bytes) => {
    if (!bytes) {
      return "0 Bytes";
    }

    const sizes = [
      "Bytes",
      "KB",
      "MB",
      "GB",
    ];

    const index = Math.floor(
      Math.log(bytes) / Math.log(1024)
    );

    return `${(
      bytes /
      Math.pow(1024, index)
    ).toFixed(2)} ${sizes[index]}`;
  };

  // ============================================================
  // OPEN REPORT VIEWER
  // ============================================================

  const openReportViewer = async (
    path,
    type
  ) => {
    const filename = getFilename(path);
    const url = getReportUrl(path);

    setViewerOpen(true);
    setViewerType(type);
    setViewerTitle(filename);
    setViewerUrl(url);
    setViewerContent("");
    setViewerLoading(true);

    try {
      // ----------------------------------------
      // PDF
      // ----------------------------------------

      if (type === "pdf") {
        setViewerLoading(false);
        return;
      }

      // ----------------------------------------
      // JSON / Markdown
      // ----------------------------------------

      const response = await axios.get(url, {
        responseType: "text",
      });

      if (type === "json") {
        try {
          const parsed = JSON.parse(
            response.data
          );

          setViewerContent(
            JSON.stringify(
              parsed,
              null,
              2
            )
          );
        } catch {
          setViewerContent(
            response.data
          );
        }
      } else {
        setViewerContent(
          response.data
        );
      }

    } catch (error) {
      console.error(
        "Failed to load report:",
        error
      );

      setViewerContent(
        "Unable to load this report. Please try downloading it instead."
      );
    } finally {
      setViewerLoading(false);
    }
  };

  // ============================================================
  // CLOSE REPORT VIEWER
  // ============================================================

  const closeReportViewer = () => {
    setViewerOpen(false);
    setViewerType("");
    setViewerTitle("");
    setViewerContent("");
    setViewerUrl("");
  };

  // ============================================================
  // REPORT TYPE
  // ============================================================

  const getReportType = (filename) => {
    const lower =
      filename.toLowerCase();

    if (lower.endsWith(".pdf")) {
      return "pdf";
    }

    if (lower.endsWith(".json")) {
      return "json";
    }

    return "markdown";
  };

  // ============================================================
  // RENDER
  // ============================================================

  return (
    <div className="app">

      {/* ======================================================
          HEADER
      ====================================================== */}

      <header className="header">
        <div>
          <h1>
            Agentic Data Parsing Engine
          </h1>

          <p>
            AI-powered data ingestion,
            processing and diagnostics
          </p>
        </div>

        <div className="system-status">
          <span className="status-dot"></span>
          Backend Online
        </div>
      </header>


      <main className="container">

        {/* ====================================================
            STATISTICS
        ==================================================== */}

        <section className="stats-grid">

          <div className="stat-card">
            <div className="stat-icon">
              📄
            </div>

            <div>
              <p>Total Reports</p>
              <h2>
                {reports.length}
              </h2>
            </div>
          </div>


          <div className="stat-card">
            <div className="stat-icon">
              ✅
            </div>

            <div>
              <p>Processed Files</p>
              <h2>
                {processedFiles.length}
              </h2>
            </div>
          </div>


          <div className="stat-card">
            <div className="stat-icon">
              ⚠️
            </div>

            <div>
              <p>Rejected Files</p>
              <h2>
                {rejectedFiles.length}
              </h2>
            </div>
          </div>


          <div className="stat-card">
            <div className="stat-icon">
              🤖
            </div>

            <div>
              <p>AI Engine</p>
              <h2>Ollama</h2>
            </div>
          </div>

        </section>


        {/* ====================================================
            UPLOAD
        ==================================================== */}

        <section className="card upload-card">

          <div className="section-heading">

            <div>
              <h2>
                Upload & Process Data
              </h2>

              <p>
                Upload a CSV file and let
                the AI pipeline analyze it.
              </p>
            </div>

          </div>


          <div className="upload-box">

            <div className="upload-icon">
              📁
            </div>

            <h3>
              {file
                ? file.name
                : "Select a CSV file to process"}
            </h3>

            {file && (
              <p className="file-size">
                {formatFileSize(file.size)}
              </p>
            )}

            <label className="file-button">
              Choose CSV File

              <input
                type="file"
                accept=".csv"
                onChange={
                  handleFileChange
                }
                hidden
              />
            </label>

          </div>


          {/* ==================================================
              PROCESS BUTTON
          ================================================== */}

          <button
            className="process-button"
            onClick={handleUpload}
            disabled={
              loading || !file
            }
          >

            {loading ? (
              <>
                <span className="spinner"></span>

                Processing...
              </>
            ) : (
              "Process File"
            )}

          </button>


          {/* ==================================================
              STATUS
          ================================================== */}

          {status && (
            <div
              className={`status ${
                status.includes(
                  "successfully"
                )
                  ? "success"
                  : status.includes(
                      "failed"
                    ) ||
                    status.includes(
                      "Failed"
                    )
                  ? "error"
                  : "processing"
              }`}
            >
              {status}
            </div>
          )}


          {/* ==================================================
              ASYNC JOB PROGRESS
          ================================================== */}

          {jobId && (
            <div className="job-progress">

              <div className="job-header">

                <strong>
                  Processing Job
                </strong>

                <span>
                  {progress}%
                </span>

              </div>


              <div className="progress-bar">

                <div
                  className="progress-fill"
                  style={{
                    width: `${progress}%`,
                  }}
                ></div>

              </div>


              <div className="job-details">

                <span>
                  Job ID:
                </span>

                <code>
                  {jobId}
                </code>

              </div>


              {jobStatus && (
                <div className="job-stage">

                  <strong>
                    Status:
                  </strong>{" "}

                  {jobStatus.stage ||
                    jobStatus.status ||
                    "Processing"}

                </div>
              )}

            </div>
          )}

        </section>


        {/* ====================================================
            PROCESSING RESULT
        ==================================================== */}

        {result && (

          <section className="card result-card">

            <div className="section-heading">

              <div>

                <h2>
                  Processing Result
                </h2>

                <p>
                  Latest pipeline execution
                  result
                </p>

              </div>


              <span className="completed-badge">
                ✓{" "}
                {result.status ||
                  "Completed"}
              </span>

            </div>


            <div className="result-grid">

              <div className="result-item">

                <strong>
                  Status
                </strong>

                <span>
                  {result.status ||
                    "Completed"}
                </span>

              </div>


              <div className="result-item">

                <strong>
                  AI Diagnostic
                </strong>

                <span>
                  {result.message ||
                    "Processing completed"}
                </span>

              </div>


              <div className="result-item">

                <strong>
                  Original File
                </strong>

                <span>
                  {getFilename(
                    result.original_file
                  ) || "-"}
                </span>

              </div>


              <div className="result-item">

                <strong>
                  Processed File
                </strong>

                <span>
                  {getFilename(
                    result.processed_file
                  ) || "-"}
                </span>

              </div>

            </div>


            {/* ==================================================
                GENERATED REPORTS
            ================================================== */}

            <h3 className="reports-title">
              Generated Reports
            </h3>


            <div className="reports">

              {result.analysis_json && (
                <>

                  <button
                    className="view-button"
                    onClick={() =>
                      openReportViewer(
                        result.analysis_json,
                        "json"
                      )
                    }
                  >
                    View JSON
                  </button>


                  <a
                    className="download-button"
                    href={getReportUrl(
                      result.analysis_json
                    )}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Download JSON
                  </a>

                </>
              )}


              {result.markdown_report && (
                <>

                  <button
                    className="view-button"
                    onClick={() =>
                      openReportViewer(
                        result.markdown_report,
                        "markdown"
                      )
                    }
                  >
                    View Markdown
                  </button>


                  <a
                    className="download-button"
                    href={getReportUrl(
                      result.markdown_report
                    )}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Download Markdown
                  </a>

                </>
              )}


              {result.pdf_report && (
                <>

                  <button
                    className="view-button"
                    onClick={() =>
                      openReportViewer(
                        result.pdf_report,
                        "pdf"
                      )
                    }
                  >
                    View PDF
                  </button>


                  <a
                    className="download-button pdf"
                    href={getReportUrl(
                      result.pdf_report
                    )}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Download PDF
                  </a>

                </>
              )}

            </div>

          </section>
        )}


        {/* ====================================================
            REPORT HISTORY
        ==================================================== */}

        <section className="card">

          <div className="section-heading">

            <div>

              <h2>
                Recent Reports
              </h2>

              <p>
                Reports generated by the
                diagnostic pipeline
              </p>

            </div>


            <button
              className="refresh-button"
              onClick={
                fetchDashboardData
              }
            >
              ↻ Refresh
            </button>

          </div>


          {reports.length === 0 ? (

            <div className="empty-state">
              No reports generated yet.
            </div>

          ) : (

            <div className="report-list">

              {reports
                .slice(0, 10)
                .map((report) => {

                  const filename =
                    report.filename;

                  const type =
                    getReportType(
                      filename
                    );

                  return (

                    <div
                      className="report-row"
                      key={filename}
                    >

                      <div className="report-info">

                        <div className="report-icon">

                          {type === "pdf"
                            ? "📕"
                            : type === "json"
                            ? "📋"
                            : "📝"}

                        </div>


                        <div>

                          <strong>
                            {filename}
                          </strong>

                          <small>
                            {
                              report.modified_time
                            }{" "}
                            •{" "}
                            {formatFileSize(
                              report.size_bytes
                            )}
                          </small>

                        </div>

                      </div>


                      <div className="report-actions">

                        <button
                          className="small-view"
                          onClick={() =>
                            openReportViewer(
                              filename,
                              type
                            )
                          }
                        >
                          View
                        </button>


                        <a
                          className="small-download"
                          href={getReportUrl(
                            filename
                          )}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Download
                        </a>

                      </div>

                    </div>

                  );
                })}

            </div>

          )}

        </section>

      </main>


      {/* ======================================================
          FOOTER
      ====================================================== */}

      <footer>

        <p>
          Agentic Data Parsing Engine •
          AI Diagnostics Platform
        </p>

      </footer>


      {/* ======================================================
          REPORT VIEWER MODAL
      ====================================================== */}

      {viewerOpen && (

        <div
          className="viewer-overlay"
          onClick={(event) => {

            if (
              event.target ===
              event.currentTarget
            ) {
              closeReportViewer();
            }

          }}
        >

          <div className="viewer-modal">

            <div className="viewer-header">

              <div>

                <h2>
                  {viewerTitle}
                </h2>

                <span className="viewer-type">
                  {viewerType.toUpperCase()} REPORT
                </span>

              </div>


              <button
                className="close-viewer"
                onClick={
                  closeReportViewer
                }
              >
                ✕
              </button>

            </div>


            <div className="viewer-content">

              {viewerLoading ? (

                <div className="viewer-loading">

                  <span className="large-spinner"></span>

                  <p>
                    Loading report...
                  </p>

                </div>

              ) : viewerType === "pdf" ? (

                <iframe
                  src={viewerUrl}
                  title={viewerTitle}
                  className="pdf-viewer"
                />

              ) : viewerType === "json" ? (

                <pre className="json-viewer">
                  {viewerContent}
                </pre>

              ) : (

                <article className="markdown-viewer">

                  <ReactMarkdown>
                    {viewerContent}
                  </ReactMarkdown>

                </article>

              )}

            </div>


            <div className="viewer-footer">

              <a
                className="download-button"
                href={viewerUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                Download Report
              </a>


              <button
                className="close-button"
                onClick={
                  closeReportViewer
                }
              >
                Close
              </button>

            </div>

          </div>

        </div>

      )}

    </div>
  );
}

export default App;