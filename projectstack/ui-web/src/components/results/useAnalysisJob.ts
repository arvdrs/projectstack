import { useEffect, useState } from 'react'
import { getAnalysisJob, type AnalysisJob } from '../../api/client'

/** Polls an analysis job once a second until it is done or failed. */
export function useAnalysisJob(initial: AnalysisJob | null): AnalysisJob | null {
  const [job, setJob] = useState<AnalysisJob | null>(initial)

  useEffect(() => { setJob(initial) }, [initial])

  useEffect(() => {
    if (!job || job.status === 'done' || job.status === 'failed') return
    const timer = setTimeout(async () => {
      try {
        setJob(await getAnalysisJob(job.id))
      } catch (e) {
        setJob({ ...job, status: 'failed', error: String(e instanceof Error ? e.message : e) })
      }
    }, 1000)
    return () => clearTimeout(timer)
  }, [job])

  return job
}
