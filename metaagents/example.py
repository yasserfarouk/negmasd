"""
Author: Jaun van Heerden

Description:
Analyzes air filter differential pressure data to predict maintenance needs.
Processes four PI tags:
- CL - Air Filter Differential Pressure Inner Left
- CL - Air Filter Differential Pressure Inner Right  
- CL - Air Filter Differential Pressure Outer Left
- CL - Air Filter Differential Pressure Outer Right

Algorithm:
1. Filter data to values > 0.1 kPa (already summarized to 15min max intervals from OSI PI)
2. Filter to 95th percentile of all data
3. Perform linear regression on 95th percentile values
4. Apply prediction interval 7 days out with alpha = 0.1
5. Alert if upper bound exceeds 7.5 kPa

Changelog
---
06/06 - Initial implementation for air filter restriction monitoring
19/06 - Added datastream driven-config
"""

from typing import List, Dict, Any, Tuple
import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta
import math

config = {}

def on_create(data: dict) -> dict | None:
    """
    Initialize with configuration.
    
    Args:
        data (dict): Must include a 'config' key with:
            - filter_threshold (float): Minimum pressure value to include (default: 0.1)
            - percentile (float): Percentile to filter to (default: 95)
            - prediction_days (int): Days ahead to predict (default: 7)
            - alpha (float): Confidence level for prediction interval (default: 0.1)
            - alert_threshold (float): Upper bound threshold for alerts (default: 7.5)
            
    Returns:
        dict | None: Loaded config keys.
    """
    global config
    config_str = data.get("config", {})
    try:
        config = json.loads(config_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON config: {e}")
    
    # Set defaults if not provided
    config.setdefault("filter_threshold", 0.1)
    config.setdefault("percentile", 95)
    config.setdefault("prediction_days", 7)
    config.setdefault("alpha", 0.1)
    config.setdefault("alert_threshold", 7.5)
    
    return {"config_loaded": list(config.keys())}


def filter_data_by_threshold(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """
    Filter dataframe to only include rows where all pressure values > threshold.
    
    Args:
        df: DataFrame with pressure columns
        threshold: Minimum pressure value
        
    Returns:
        Filtered DataFrame
    """
    pressure_cols = [col for col in df.columns if col not in ['ts', 'entity_id']]
    
    # Create mask for rows where all pressure values > threshold
    mask = True
    for col in pressure_cols:
        mask = mask & (df[col] > threshold)
    
    return df[mask]


def calculate_percentiles(df: pd.DataFrame, percentile: float) -> pd.DataFrame:
    """
    Calculate percentile values for all data.
    
    Args:
        df: DataFrame with timestamp and pressure data
        percentile: Percentile to calculate (e.g., 95)
        
    Returns:
        DataFrame with percentile values
    """
    df = df.copy()
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.sort_values('ts')
    
    pressure_cols = [col for col in df.columns if col not in ['ts', 'entity_id']]
    
    # Calculate percentiles for each column
    result_rows = []
    for _, row in df.iterrows():
        new_row = {'ts': row['ts']}
        for col in pressure_cols:
            # For each timestamp, use the 95th percentile value if current value >= percentile threshold
            col_percentile = np.percentile(df[col].dropna(), percentile)
            if row[col] >= col_percentile:
                new_row[col] = row[col]
        
        # Only include rows where at least one field meets percentile criteria
        if len(new_row) > 1:  # More than just timestamp
            result_rows.append(new_row)
    
    return pd.DataFrame(result_rows)


def t_distribution_critical_value(alpha: float, df: int) -> float:
    """
    Approximate t-distribution critical value for given alpha and degrees of freedom.
    Uses normal approximation for df > 30, otherwise uses hardcoded values.
    """
    if df > 30:
        # Use normal approximation for large df
        z_values = {0.1: 1.645, 0.05: 1.96, 0.01: 2.576}
        return z_values.get(alpha/2, 1.96)  # Default to 95% confidence
    
    # Hardcoded t-values for common alpha levels and small df
    t_table = {
        1: {0.1: 6.314, 0.05: 12.706, 0.01: 63.657},
        2: {0.1: 2.920, 0.05: 4.303, 0.01: 9.925},
        3: {0.1: 2.353, 0.05: 3.182, 0.01: 5.841},
        4: {0.1: 2.132, 0.05: 2.776, 0.01: 4.604},
        5: {0.1: 2.015, 0.05: 2.571, 0.01: 4.032},
        10: {0.1: 1.812, 0.05: 2.228, 0.01: 3.169},
        15: {0.1: 1.753, 0.05: 2.131, 0.01: 2.947},
        20: {0.1: 1.725, 0.05: 2.086, 0.01: 2.845},
        30: {0.1: 1.697, 0.05: 2.042, 0.01: 2.750}
    }
    
    # Find closest df in table
    available_df = sorted(t_table.keys())
    closest_df = min(available_df, key=lambda x: abs(x - df))
    
    return t_table[closest_df].get(alpha, t_table[closest_df][0.05])


def perform_linear_regression(df: pd.DataFrame, field: str, prediction_days: int, alpha: float) -> Dict[str, Any]:
    """
    Perform linear regression using simple math (no sklearn).
    
    Args:
        df: DataFrame with timestamp and field data
        field: Field name to analyze
        prediction_days: Days ahead to predict
        alpha: Confidence level for prediction interval
        
    Returns:
        Dictionary with regression results and predictions
    """
    if len(df) < 2:
        return {"error": f"Insufficient data for regression on {field}"}
    
    # Convert timestamps to numerical values (days since start)
    df = df.copy()
    df['ts'] = pd.to_datetime(df['ts'])
    start_time = df['ts'].min()
    df['days'] = (df['ts'] - start_time).dt.total_seconds() / (24 * 3600)
    
    # Get x and y values
    x_values = df['days'].values
    y_values = df[field].values
    
    # Remove any NaN values
    valid_indices = ~(np.isnan(x_values) | np.isnan(y_values))
    x_values = x_values[valid_indices]
    y_values = y_values[valid_indices]
    
    if len(x_values) < 2:
        return {"error": f"Insufficient valid data for regression on {field}"}
    
    n = len(x_values)
    
    # Calculate linear regression using basic math
    sum_x = sum(x_values)
    sum_y = sum(y_values)
    sum_xy = sum(x * y for x, y in zip(x_values, y_values))
    sum_x_squared = sum(x * x for x in x_values)
    sum_y_squared = sum(y * y for y in y_values)
    
    # Calculate slope and intercept
    denominator = n * sum_x_squared - sum_x * sum_x
    if denominator == 0:
        return {"error": f"Cannot calculate regression - no variance in x values for {field}"}
    
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    
    # Calculate prediction point
    last_day = max(x_values)
    prediction_day = last_day + prediction_days
    y_pred = slope * prediction_day + intercept
    
    # Calculate fitted values and residuals
    y_fitted = [slope * x + intercept for x in x_values]
    residuals = [y_actual - y_fit for y_actual, y_fit in zip(y_values, y_fitted)]
    
    # Calculate mean squared error
    mse = sum(r * r for r in residuals) / (n - 2) if n > 2 else 0
    
    # Calculate standard error for prediction
    x_mean = sum_x / n
    sxx = sum((x - x_mean) ** 2 for x in x_values)
    
    if sxx == 0:
        se_pred = math.sqrt(mse)
    else:
        se_pred = math.sqrt(mse * (1 + 1/n + (prediction_day - x_mean)**2 / sxx))
    
    # Calculate confidence interval
    df_residual = n - 2
    t_value = t_distribution_critical_value(alpha, df_residual)
    margin_error = t_value * se_pred
    
    lower_bound = y_pred - margin_error
    upper_bound = y_pred + margin_error
    
    # Calculate R-squared
    y_mean = sum_y / n
    ss_tot = sum((y - y_mean) ** 2 for y in y_values)
    ss_res = sum(r * r for r in residuals)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
    
    return {
        "field": field,
        "prediction": float(y_pred),
        "lower_bound": float(lower_bound),
        "upper_bound": float(upper_bound),
        "slope": float(slope),
        "intercept": float(intercept),
        "r_squared": float(r_squared),
        "prediction_date": (start_time + timedelta(days=prediction_day)).isoformat(),
        "data_points": n
    }


def on_receive(data: list) -> dict:
    """
    Main processing entry point for air filter restriction analysis.
    
    Args:
        data (list): [{'ts': str, 'inner_left': float, 'inner_right': float, 
                      'outer_left': float, 'outer_right': float, 'entity_id': str}, ...]
                      
    Returns:
        dict: Analysis results with predictions and alerts
    """
    if not data:
        return {"result": json.dumps({"error": "No data provided"}), "entity_id": None}
    
    # Overwrite config
    keys_to_get = [
        "alert_threshold", 
        "alpha", 
        "filter_threshold", 
        "percentile",
        "prediction_days"
        ]

    config.update({k: data[0][k] for k in keys_to_get if data[0].get(k) is not None})

    entity_id = data[0].get('entity_id')
    
    # Convert to DataFrame
    df = pd.DataFrame(data)
    df['ts'] = pd.to_datetime(df['ts'])
    df = df.sort_values('ts')
    
    # Get configuration values
    filter_threshold = config.get("filter_threshold", 0.1)
    percentile = config.get("percentile", 95)
    prediction_days = config.get("prediction_days", 7)
    alpha = config.get("alpha", 0.1)
    alert_threshold = config.get("alert_threshold", 7.5)
    
    # Step 1: Filter data to values > threshold
    filtered_df = filter_data_by_threshold(df, filter_threshold)
    
    if filtered_df.empty:
        return {
            "result": json.dumps({
                "error": f"No data above threshold {filter_threshold} kPa",
                "total_records": len(df)
            }),
            "entity_id": entity_id
        }
    
    # Step 2: Calculate 95th percentile data
    percentile_df = calculate_percentiles(filtered_df, percentile)
    
    if percentile_df.empty:
        return {
            "result": json.dumps({
                "error": "No data available for percentile calculation"
            }),
            "entity_id": entity_id
        }
    
    # Step 3: Perform linear regression for each field
    pressure_fields = ['inner_left', 'inner_right', 'outer_left', 'outer_right']
    results = {
        "entity_id": entity_id,
        "analysis_timestamp": datetime.now().isoformat(),
        "data_summary": {
            "total_records": len(df),
            "filtered_records": len(filtered_df),
            "percentile_records": len(percentile_df),
            "date_range": {
                "start": df['ts'].min().isoformat(),
                "end": df['ts'].max().isoformat()
            }
        },
        "predictions": [],
        "alerts": []
    }
    
    for field in pressure_fields:
        if field in percentile_df.columns:
            regression_result = perform_linear_regression(
                percentile_df, field, prediction_days, alpha
            )
            
            if "error" not in regression_result:
                results["predictions"].append(regression_result)
                
                # Check for alerts
                if regression_result["upper_bound"] > alert_threshold:
                    alert = {
                        "field": field,
                        "alert_type": "maintenance_required",
                        "message": f"Upper prediction bound ({regression_result['upper_bound']:.2f} kPa) exceeds threshold ({alert_threshold} kPa)",
                        "predicted_value": regression_result["prediction"],
                        "upper_bound": regression_result["upper_bound"],
                        "prediction_date": regression_result["prediction_date"],
                        "urgency": "high" if regression_result["upper_bound"] > alert_threshold * 1.2 else "medium"
                    }
                    results["alerts"].append(alert)
            else:
                results["predictions"].append(regression_result)
    
    return {
        "result": json.dumps(results),
        "entity_id": entity_id
    }


def on_destroy() -> dict | None:
    """
    Final cleanup.
    
    Returns:
        dict | None: Final config used.
    """
    return {"config_used": config}


# Test code
if __name__ == "__main__":
    # Test configuration
    test_config = {
        "filter_threshold": 0.1,
        "percentile": 95,
        "prediction_days": 7,
        "alpha": 0.1,
        "alert_threshold": 7.5
    }
    
    on_create({"config": json.dumps(test_config)})
    
    # Create test data similar to example data format
    test_data = [
        {"ts": "2025-05-24T00:00:00Z", "inner_left": 3.2, "inner_right": 3.6, "outer_left": 2.8, "outer_right": 3.3, "entity_id": "DT5406"},
        {"ts": "2025-05-24T00:15:00Z", "inner_left": 3.4, "inner_right": 3.7, "outer_left": 2.9, "outer_right": 3.4, "entity_id": "DT5406"},
        {"ts": "2025-05-24T12:00:00Z", "inner_left": 3.3, "inner_right": 3.6, "outer_left": 2.8, "outer_right": 3.3, "entity_id": "DT5406"},
        {"ts": "2025-05-24T12:15:00Z", "inner_left": 3.3, "inner_right": 3.7, "outer_left": 2.8, "outer_right": 3.3, "entity_id": "DT5406"},
        {"ts": "2025-05-25T00:00:00Z", "inner_left": 3.5, "inner_right": 3.8, "outer_left": 3.0, "outer_right": 3.4, "entity_id": "DT5406"},
        {"ts": "2025-05-25T12:00:00Z", "inner_left": 3.6, "inner_right": 3.9, "outer_left": 3.1, "outer_right": 3.5, "entity_id": "DT5406"}
    ]
    
    result = on_receive(test_data)
    
    print("Raw JSON output:")
    print(result["result"])
    print("\nPretty printed:")
    import pprint
    pprint.pprint(json.loads(result["result"]))