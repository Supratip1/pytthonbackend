from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn
import logging
from enhanced_aeo_analysis import run_full_aeo_pipeline, run_with_competitors

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="AEO Analysis API",
    description="API for Answer Engine Optimization analysis",
    version="1.0.0"
)

# Add CORS middleware for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # Add your frontend URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request model
class AnalysisRequest(BaseModel):
    url: str
    max_pages: Optional[int] = 10

# Response model
class AnalysisResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None

@app.get("/")
async def root():
    return {"message": "AEO Analysis API is running"}

@app.post("/analyze", response_model=AnalysisResponse)
async def analyze_website(request: AnalysisRequest):
    """
    Analyze a website for AEO optimization
    """
    try:
        logger.info(f"Starting AEO analysis for URL: {request.url}")
        
        # Run the AEO analysis
        results = run_full_aeo_pipeline(request.url)
        
        logger.info(f"AEO analysis completed successfully for: {request.url}")
        
        return AnalysisResponse(
            success=True,
            data=results
        )
        
    except Exception as e:
        logger.error(f"Error during AEO analysis: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {str(e)}"
        )

@app.post("/analyze_with_competitors", response_model=AnalysisResponse)
async def analyze_website_with_competitors(request: AnalysisRequest):
    """
    Analyze a website for AEO optimization and competitor comparison
    """
    try:
        logger.info(f"Starting AEO analysis with competitors for URL: {request.url}")
        results = run_with_competitors(request.url)
        logger.info(f"AEO analysis with competitors completed successfully for: {request.url}")
        return AnalysisResponse(
            success=True,
            data=results
        )
    except Exception as e:
        logger.error(f"Error during AEO analysis with competitors: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Analysis with competitors failed: {str(e)}"
        )

@app.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    return {"status": "healthy", "service": "AEO Analysis API"}

if __name__ == "__main__":
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    ) 