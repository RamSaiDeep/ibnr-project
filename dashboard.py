"""
Interactive IBNR dashboard.

Run with:
    python dashboard.py

Then open http://127.0.0.1:8050 in a browser.

For Render deployment:
    - The app will use the PORT environment variable automatically
    - File upload is available in the UI

Layout:
    - File upload control
    - Tabs: Triangles | Development Factors | Ultimates & IBNR
    - Triangle heatmap + selectable measure (claims / counts / severity)
    - Development factor line chart across development age
    - Ultimate vs. reported bar chart with IBNR highlighted
"""

from __future__ import annotations

import os
import base64
import io

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, dash_table

from ibnr.pipeline import run_pipeline

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RESULTS = None
UPLOAD_FOLDER = "uploads"
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

MEASURE_LABELS = {"claims": "Reported Claims ($)", "counts": "Reported Counts", "severity": "Reported Severity ($)"}
MEASURE_ORDER = ["claims", "counts", "severity"]

app = dash.Dash(__name__)
app.title = "IBNR Chain-Ladder Wizard"

# Store for user-selected development factors
app.layout = html.Div(
    style={"fontFamily": "Segoe UI, sans-serif", "margin": "20px"},
    children=[
        html.H1("IBNR Chain-Ladder Wizard", style={"marginBottom": "4px"}),
        
        # File upload section
        html.Div([
            html.P("Upload your claims Excel file to begin:", style={"marginBottom": "8px"}),
            dcc.Upload(
                id="upload-data",
                children=html.Div([
                    "Drag and Drop or ",
                    html.A("Select File")
                ]),
                style={
                    "width": "100%",
                    "height": "60px",
                    "lineHeight": "60px",
                    "borderWidth": "1px",
                    "borderStyle": "dashed",
                    "borderRadius": "5px",
                    "textAlign": "center",
                    "margin": "10px 0",
                    "backgroundColor": "#f8f9fa"
                },
                multiple=False,
                accept=".xlsx,.xls"
            ),
            html.Div(id="upload-status", style={"color": "#666", "marginBottom": "20px"}),
        ]),
        
        # Progress indicator (hidden until file uploaded)
        html.Div(id="progress-bar", style={"marginBottom": "20px", "display": "none"}),
        
        # Step content (hidden until file uploaded)
        html.Div(id="step-content", style={"minHeight": "400px", "marginBottom": "20px"}),
        
        # Anchor for scrolling to top
        html.Div(id="page-top"),
        
        # Navigation buttons - always present, visibility controlled via CSS
        html.Div([
            html.Button("Previous", id="prev-btn", n_clicks=0,
                      style={"padding": "8px 16px"}),
            html.Button("Next", id="next-btn", n_clicks=0,
                      style={"padding": "8px 16px", "marginLeft": "8px"}),
            html.Button("Restart", id="restart-btn", n_clicks=0,
                      style={"padding": "8px 16px", "marginLeft": "8px", "backgroundColor": "#28a745", "color": "white", "border": "none"}),
        ], id="navigation-buttons", style={"marginTop": "20px", "display": "none"}),
        
        # Store for user selections and file data
        dcc.Store(id="user-selections", data={}),
        dcc.Store(id="current-step", data=0),
        dcc.Store(id="file-data", data=None),
   ],
)


def triangle_to_table(df: pd.DataFrame) -> dash_table.DataTable:
    display_df = df.reset_index().rename(columns={"index": "AY"})
    return dash_table.DataTable(
        data=display_df.round(2).to_dict("records"),
        columns=[{"name": str(c), "id": str(c)} for c in display_df.columns],
        style_table={"overflowX": "auto", "width": "100%"},
        style_cell={"textAlign": "right", "padding": "8px", "fontFamily": "monospace", "fontSize": "14px", "minWidth": "80px", "border": "1px solid #ddd"},
        style_header={"fontWeight": "bold", "backgroundColor": "#f0f2f5", "border": "1px solid #ddd"},
        style_data={"border": "1px solid #ddd"},
        style_data_conditional=[
            {"if": {"column_id": "AY"}, "textAlign": "left", "fontWeight": "bold"}
        ],
    )


def triangle_heatmap(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure(
        data=go.Heatmap(
            z=df.values,
            x=[str(c) for c in df.columns],
            y=[str(i) for i in df.index],
            colorscale="Blues",
            hoverongaps=False,
            text=[[f"{val:,.0f}" if pd.notna(val) else "" for val in row] for row in df.values],
            texttemplate="%{text}",
            textfont={"size": 11, "color": "black"},
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title="Development Age (months)",
        yaxis_title="Accident Year",
        yaxis=dict(autorange="reversed"),
        margin=dict(t=50, l=60, r=20, b=40),
    )
    return fig


def age_to_age_scatter(ata_df: pd.DataFrame, title: str) -> go.Figure:
    """Plot age-to-age factors with (0,0) origin."""
    fig = go.Figure()
    
    # Add (0,0) point
    fig.add_trace(go.Scatter(x=[0], y=[0], mode='markers', name='Origin (0,0)', 
                             marker=dict(color='red', size=12, symbol='cross')))
    
    # Plot each development period's factors
    for col in ata_df.columns:
        fig.add_trace(go.Scatter(
            x=[int(col.split('-')[0]) for _ in range(len(ata_df[col]))],
            y=ata_df[col].values,
            mode='markers+lines',
            name=col,
        ))
    
    fig.update_layout(
        title=title,
        xaxis_title="Development Age (months)",
        yaxis_title="Age-to-Age Factor",
        margin=dict(t=50, l=60, r=20, b=40),
        showlegend=True,
    )
    return fig


# Total pages: 3 measures (claims, counts, severity) + 1 final results page
# Each measure page shows: triangle + age-to-age table + age-to-age plot + LDF selection
TOTAL_STEPS = 4


@app.callback(
    Output("file-data", "data"),
    Output("upload-status", "children"),
    Output("progress-bar", "style"),
    Output("navigation-buttons", "style"),
    Input("upload-data", "contents"),
    State("upload-data", "filename"),
)
def handle_upload(contents, filename):
    """Handle file upload and process the data."""
    if contents is None:
        return None, "No file uploaded yet", {"display": "none"}, {"display": "none"}
    
    try:
        # Parse uploaded file
        content_type, content_string = contents.split(",")
        decoded = base64.b64decode(content_string)
        
        # Save to file for pipeline processing
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        with open(filepath, "wb") as f:
            f.write(decoded)
        
        # Run pipeline
        results = run_pipeline(filepath, dev_method=None)
        
        # Convert results to JSON-serializable format
        file_data = {
            "filename": filename,
            "triangles": {
                measure: df.to_dict() for measure, df in results["triangles"].items()
            },
            "age_to_age": {
                measure: df.to_dict() for measure, df in results["age_to_age"].items()
            }
        }
        
        return file_data, f"File '{filename}' uploaded successfully!", {"display": "block"}, {"display": "block"}
    except Exception as e:
        return None, f"Error processing file: {str(e)}", {"display": "none"}, {"display": "none"}


@app.callback(
    Output("progress-bar", "children"),
    Output("step-content", "children"),
    Output("prev-btn", "style"),
    Output("next-btn", "style"),
    Output("restart-btn", "style"),
    Input("current-step", "data"),
    Input("user-selections", "data"),
    Input("file-data", "data"),
)
def render_step(step, selections, file_data):
    # If no file uploaded, show placeholder
    if file_data is None:
        empty_content = html.Div([
            html.P("Please upload a file to begin.", style={"color": "#666", "fontSize": "16px"})
        ])
        return html.Div(), empty_content, {"visibility": "hidden"}, {"visibility": "hidden"}, {"visibility": "hidden"}
    
    # Convert file_data back to DataFrames
    triangles = {
        measure: pd.DataFrame(data) for measure, data in file_data["triangles"].items()
    }
    age_to_age = {
        measure: pd.DataFrame(data) for measure, data in file_data["age_to_age"].items()
    }
    
    # Calculate progress
    progress_pct = (step + 1) / TOTAL_STEPS * 100
    progress_bar = html.Div([
        html.Div(style={
            "width": f"{progress_pct}%",
            "height": "8px",
            "backgroundColor": "#007bff",
            "borderRadius": "4px",
        })
    ], style={
        "width": "100%",
        "height": "8px",
        "backgroundColor": "#e9ecef",
        "borderRadius": "4px",
    })
    
    # Render content based on step
    if step < 3:
        # Pages 0-2: Claims, Counts, Severity (each with all components)
        measure = MEASURE_ORDER[step]
        ata_df = age_to_age[measure]
        tri = triangles[measure]
        
        # Calculate suggested values
        from ibnr.triangles import development_factors, volume_weighted_development_factors
        simple_avg = development_factors(ata_df, method="simple_average")
        vol_weighted = volume_weighted_development_factors(tri)
        
        content = html.Div([
            html.H3(f"Page {step + 1}: {MEASURE_LABELS[measure]}", style={"marginBottom": "16px"}),
            
            # Triangle with values
            html.H4("Triangle", style={"marginTop": "16px"}),
            dcc.Graph(figure=triangle_heatmap(tri, MEASURE_LABELS[measure])),
            
            # Age-to-age factors table
            html.H4("Age-to-Age Factors", style={"marginTop": "24px"}),
            triangle_to_table(ata_df),
            
            # Age-to-age factors plot with (0,0)
            html.H4("Age-to-Age Factors Plot", style={"marginTop": "24px"}),
            dcc.Graph(figure=age_to_age_scatter(ata_df, f"Age-to-Age Factors - {MEASURE_LABELS[measure]}")),
            
            # LDF selection
            html.H4("Select Development Factors", style={"marginTop": "24px"}),
            html.P("Choose your loss development factors for each development period:", style={"marginBottom": "16px"}),
            html.Div([
                html.H5("Suggested Values:", style={"marginBottom": "8px"}),
                html.Div([
                    html.Strong("Simple Average: "),
                    html.Code(f"{simple_avg.to_dict()}"),
                ], style={"marginBottom": "8px"}),
                html.Div([
                    html.Strong("Volume Weighted: "),
                    html.Code(f"{vol_weighted.to_dict()}"),
                ], style={"marginBottom": "16px"}),
            ]),
            html.H5("Your Selection:", style={"marginBottom": "8px"}),
            html.Div(id=f"devfac-inputs-{measure}", children=[
                html.Div([
                    html.Label(f"{col}: ", style={"marginRight": "8px"}),
                    dcc.Input(
                        id={"type": "devfac-input", "measure": measure, "period": col},
                        type="number",
                        value=float(simple_avg[col]) if col in simple_avg.index else 1.0,
                        step=0.001,
                        style={"width": "100px", "marginRight": "16px"},
                    ),
                ], style={"display": "inline-block", "marginBottom": "8px"})
                for col in ata_df.columns
            ]),
        ])
    else:
        # Page 3: Show ultimate & IBNR results
        # Recalculate with user selections
        from ibnr.triangles import development_factors, project_ultimates, combine_frequency_severity
        
        # Build custom development factors from user selections
        custom_devfacs = {}
        for measure in MEASURE_ORDER:
            if measure in selections and selections[measure]:
                # Use user's selections
                custom_devfacs[measure] = pd.Series(selections[measure])
            else:
                # Fallback to simple average from age-to-age factors
                custom_devfacs[measure] = development_factors(age_to_age[measure], method="simple_average")
        
        # Recalculate ultimates with custom factors
        ult_claims_chainladder = project_ultimates(triangles["claims"], custom_devfacs["claims"], label="Ultimate_Claims")
        ult_counts = project_ultimates(triangles["counts"], custom_devfacs["counts"], label="Ultimate_Counts")
        ult_severity = project_ultimates(triangles["severity"], custom_devfacs["severity"], label="Ultimate_Severity")
        ult_claims_freqsev = combine_frequency_severity(ult_counts, ult_severity, scale=1000.0)
        
        comparison = pd.DataFrame(index=triangles["claims"].index)
        comparison["Latest_Reported_Claims"] = ult_claims_chainladder["Latest_Reported"]
        comparison["Ultimate_ChainLadder"] = ult_claims_chainladder["Ultimate_Claims"]
        comparison["Ultimate_FreqSeverity"] = ult_claims_freqsev["Ultimate_Claims_FreqSev"]
        comparison["IBNR_ChainLadder"] = ult_claims_chainladder["IBNR"]
        comparison["IBNR_FreqSeverity"] = ult_claims_freqsev["Ultimate_Claims_FreqSev"] - comparison["Latest_Reported_Claims"]
        
        content = html.Div([
            html.H3("Page 4: Ultimate Claims & IBNR Results", style={"marginBottom": "16px"}),
            html.P("Chain-ladder applied directly to the aggregate claims triangle, vs. "
                   "the frequency-severity technique (chain-ladder run separately on "
                   "counts and severity, then multiplied together).", style={"color": "#555", "marginBottom": "16px"}),
            
            # Ultimate comparison chart
            dcc.Graph(figure=go.Figure(data=[
                go.Bar(x=[str(i) for i in comparison.index], y=comparison["Ultimate_ChainLadder"],
                       name="Chain-Ladder (on claims)"),
                go.Bar(x=[str(i) for i in comparison.index], y=comparison["Ultimate_FreqSeverity"],
                       name="Frequency-Severity"),
                go.Scatter(x=[str(i) for i in comparison.index], y=comparison["Latest_Reported_Claims"],
                          name="Latest Reported", mode="markers",
                          marker=dict(color="black", symbol="line-ew", size=14, line=dict(width=2))),
            ], layout=dict(
                barmode="group",
                title="Ultimate Claims: Chain-Ladder vs. Frequency-Severity",
                xaxis_title="Accident Year",
                yaxis_title="Amount",
                margin=dict(t=50, l=60, r=20, b=40),
            ))),
            
            html.H4("Ultimate Claims Comparison by Accident Year", style={"marginTop": "24px"}),
            triangle_to_table(comparison),
            
            html.H4("Frequency-Severity Detail", style={"marginTop": "24px"}),
            triangle_to_table(ult_claims_freqsev),
        ])
    
    # Navigation button visibility
    if step == 0:
        prev_style = {"visibility": "hidden", "padding": "8px 16px"}
        next_style = {"padding": "8px 16px", "marginLeft": "8px"}
        restart_style = {"visibility": "hidden", "padding": "8px 16px", "marginLeft": "8px", "backgroundColor": "#28a745", "color": "white", "border": "none"}
    elif step == TOTAL_STEPS - 1:
        prev_style = {"padding": "8px 16px"}
        next_style = {"visibility": "hidden", "padding": "8px 16px", "marginLeft": "8px"}
        restart_style = {"padding": "8px 16px", "marginLeft": "8px", "backgroundColor": "#28a745", "color": "white", "border": "none"}
    else:
        prev_style = {"padding": "8px 16px"}
        next_style = {"padding": "8px 16px", "marginLeft": "8px"}
        restart_style = {"visibility": "hidden", "padding": "8px 16px", "marginLeft": "8px", "backgroundColor": "#28a745", "color": "white", "border": "none"}
    
    return progress_bar, content, prev_style, next_style, restart_style


@app.callback(
    Output("user-selections", "data", allow_duplicate=True),
    Input({"type": "devfac-input", "measure": dash.ALL, "period": dash.ALL}, "value"),
    State({"type": "devfac-input", "measure": dash.ALL, "period": dash.ALL}, "id"),
    State("user-selections", "data"),
    prevent_initial_call=True,
)
def capture_devfac_inputs(values, ids, selections):
    """Capture user's development factor inputs."""
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update
    
    # Group inputs by measure
    for value, id_info in zip(values, ids):
        measure = id_info["measure"]
        period = id_info["period"]
        
        if measure not in selections:
            selections[measure] = {}
        selections[measure][period] = value
    
    return selections


@app.callback(
    Output("user-selections", "data", allow_duplicate=True),
    Output("current-step", "data"),
    Input("next-btn", "n_clicks"),
    Input("prev-btn", "n_clicks"),
    Input("restart-btn", "n_clicks"),
    State("current-step", "data"),
    State("user-selections", "data"),
    prevent_initial_call=True,
)
def navigate(next_clicks, prev_clicks, restart_clicks, current_step, selections):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update, dash.no_update
    
    button_id = ctx.triggered[0]["prop_id"].split(".")[0]
    
    if button_id == "next-btn":
        return selections, current_step + 1
    elif button_id == "prev-btn":
        return selections, max(0, current_step - 1)
    elif button_id == "restart-btn":
        return {}, 0
    
    return selections, current_step


# Clientside callback to scroll to top when step changes
app.clientside_callback(
    """
    function(n_clicks) {
        if (n_clicks !== null && n_clicks > 0) {
            window.scrollTo(0, 0);
        }
        return window.dash_clientside.no_update;
    }
    """,
    Output("page-top", "children"),
    Input("current-step", "data"),
)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    app.run_server(host="0.0.0.0", port=port, debug=False)
