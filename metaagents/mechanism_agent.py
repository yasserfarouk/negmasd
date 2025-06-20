"""
Mechanism Agent with MQTT Integration for Negotiation
Coordinates negotiation between buyer and seller agents
Based on the negmasd architecture with on_create, on_receive, on_destroy pattern
"""

import json
import paho.mqtt.client as mqtt
from typing import Dict, Any, List
import threading
import time
import uuid

# Global state
config = {}
mqtt_client = None
agent_topic = "mechanism"
response_topic = "mechanism_response"
buyer_topic = "buyer"
seller_topic = "seller"
negotiations = {}
round_counter = {}

def on_create(data: dict) -> dict | None:
    """
    Initialize the mechanism agent with configuration.
    
    Args:
        data (dict): Configuration data including:
            - mqtt_broker: MQTT broker address
            - mqtt_port: MQTT broker port
            - mechanism_config: Negotiation mechanism parameters
            
    Returns:
        dict | None: Initialization status
    """
    global config, mqtt_client
    
    config = data.copy()
    config.setdefault("mqtt_broker", "localhost")
    config.setdefault("mqtt_port", 1883)
    config.setdefault("name", "mechanism")
    config.setdefault("mechanism_config", {
        "max_rounds": 10,
        "timeout_seconds": 60,
        "mechanism_type": "SAOMechanism",
        "outcome_space": {
            "issues": {
                "price": {"values": [100, 150, 200, 250, 300]},
                "quantity": {"values": [1, 10]}
            }
        }
    })
    
    # Initialize MQTT client
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    
    try:
        mqtt_client.connect(config["mqtt_broker"], config["mqtt_port"], 60)
        mqtt_client.loop_start()
        return {"status": "mechanism_agent_initialized", "config": list(config.keys())}
    except Exception as e:
        return {"error": f"Failed to initialize MQTT: {str(e)}"}


def on_mqtt_connect(client, userdata, flags, rc):
    """Callback for when MQTT client connects"""
    if rc == 0:
        print(f"Mechanism agent connected to MQTT broker")
        client.subscribe(agent_topic)
        client.subscribe("buyer_response")
        client.subscribe("seller_response")
    else:
        print(f"Failed to connect to MQTT broker: {rc}")


def on_mqtt_message(client, userdata, msg):
    """Handle incoming MQTT messages and route to mechanism processing"""
    try:
        message_data = json.loads(msg.payload.decode())
        topic = msg.topic
        
        # Process message through mechanism logic
        if topic == agent_topic:
            response = on_receive([message_data])
        elif topic in ["buyer", "seller"]:  # Listen to agent topics directly
            response = handle_agent_response(message_data, topic)
        else:
            response = {"error": f"Unknown topic: {topic}"}
        
        # Send response back via MQTT if needed and topic specified
        if response and "result" in response and "topic" in response:
            client.publish(response["topic"], response["result"])
            
    except Exception as e:
        error_response = {"error": f"Message processing failed: {str(e)}"}
        client.publish(response_topic, json.dumps(error_response))


def on_receive(data: list) -> dict:
    """
    Main processing entry point for mechanism agent.
    
    Args:
        data (list): List of negotiation control messages
                    
    Returns:
        dict: Mechanism's response and coordination actions, including target topic
    """
    if not data:
        return {"result": json.dumps({"error": "No data provided"}), "agent": "mechanism", "topic": "mechanism_response"}
    
    message = data[0]
    message_type = message.get("type", "unknown")
    from_agent = message.get("from_agent", "external")
    
    response = {
        "agent": "mechanism",
        "timestamp": time.time()
    }
    
    if message_type == "start_negotiation":
        response.update(start_negotiation(message))
        target_topic = "mechanism_response"  # Respond to external caller
    elif message_type == "end_negotiation":
        response.update(end_negotiation(message))
        target_topic = "mechanism_response"
    elif message_type == "get_status":
        response.update(get_negotiation_status(message))
        target_topic = "mechanism_response"
    else:
        response.update({"error": f"Unknown message type: {message_type}"})
        target_topic = "mechanism_response"
    
    return {"result": json.dumps(response), "agent": "mechanism", "topic": target_topic}


def start_negotiation(message: dict) -> dict:
    """Start a new negotiation session between buyer and seller"""
    session_id = message.get("session_id", str(uuid.uuid4()))
    participants = message.get("participants", ["buyer", "seller"])
    
    mechanism_config = config["mechanism_config"]
    outcome_space = message.get("outcome_space", mechanism_config["outcome_space"])
    
    # Initialize negotiation session
    negotiations[session_id] = {
        "status": "active",
        "participants": participants,
        "outcome_space": outcome_space,
        "start_time": time.time(),
        "rounds": [],
        "current_round": 0,
        "max_rounds": mechanism_config["max_rounds"],
        "final_agreement": None
    }
    
    round_counter[session_id] = 0
    
    # Send start message to both agents
    start_message = {
        "type": "start_negotiation",
        "session_id": session_id,
        "outcome_space": outcome_space,
        "mechanism": "SAOMechanism"
    }
    
    mqtt_client.publish(buyer_topic, json.dumps(start_message))
    mqtt_client.publish(seller_topic, json.dumps(start_message))
    
    return {
        "type": "negotiation_started",
        "session_id": session_id,
        "participants": participants,
        "status": "active",
        "message": "Negotiation session started"
    }


def handle_agent_response(message: dict, source_topic: str) -> dict:
    """Handle responses from buyer or seller agents"""
    session_id = message.get("session_id")
    message_type = message.get("type")
    
    if session_id not in negotiations:
        return {"error": "Unknown negotiation session", "topic": "mechanism_response"}
    
    negotiation = negotiations[session_id]
    agent_name = "buyer" if "buyer" in source_topic else "seller"
    other_agent = "seller" if agent_name == "buyer" else "buyer"
    other_topic = seller_topic if agent_name == "buyer" else buyer_topic
    
    # Record the round
    round_info = {
        "round": round_counter[session_id],
        "agent": agent_name,
        "message_type": message_type,
        "timestamp": time.time(),
        "content": message
    }
    
    negotiation["rounds"].append(round_info)
    
    if message_type == "offer":
        # Forward offer to other agent
        offer_message = {
            "type": "offer",
            "session_id": session_id,
            "offer": message.get("offer"),
            "round": round_counter[session_id],
            "from_agent": agent_name
        }
        
        mqtt_client.publish(other_topic, json.dumps(offer_message))
        round_counter[session_id] += 1
        
        # Check if max rounds reached
        if round_counter[session_id] >= negotiation["max_rounds"]:
            end_negotiation_message = {
                "type": "end_negotiation",
                "session_id": session_id,
                "reason": "max_rounds_reached"
            }
            mqtt_client.publish(buyer_topic, json.dumps(end_negotiation_message))
            mqtt_client.publish(seller_topic, json.dumps(end_negotiation_message))
            negotiation["status"] = "ended"
            negotiation["final_agreement"] = None
        
        return {
            "result": json.dumps({
                "type": "offer_forwarded",
                "session_id": session_id,
                "round": round_counter[session_id] - 1,
                "message": f"Offer forwarded from {agent_name} to {other_agent}"
            }),
            "topic": "mechanism_response"
        }
    
    elif message_type == "accept":
        # Agreement reached
        negotiation["status"] = "completed"
        negotiation["final_agreement"] = message.get("offer")
        
        # Notify both agents
        accept_message = {
            "type": "accept",
            "session_id": session_id,
            "final_agreement": message.get("offer"),
            "accepting_agent": agent_name
        }
        
        mqtt_client.publish(buyer_topic, json.dumps(accept_message))
        mqtt_client.publish(seller_topic, json.dumps(accept_message))
        
        return {
            "result": json.dumps({
                "type": "negotiation_completed",
                "session_id": session_id,
                "final_agreement": message.get("offer"),
                "accepting_agent": agent_name,
                "total_rounds": round_counter[session_id],
                "message": "Negotiation completed successfully"
            }),
            "topic": "mechanism_response"
        }
    
    elif message_type == "reject":
        # Rejection - end negotiation
        negotiation["status"] = "failed"
        
        # Notify both agents
        reject_message = {
            "type": "reject",
            "session_id": session_id,
            "rejecting_agent": agent_name
        }
        
        mqtt_client.publish(buyer_topic, json.dumps(reject_message))
        mqtt_client.publish(seller_topic, json.dumps(reject_message))
        
        return {
            "result": json.dumps({
                "type": "negotiation_failed",
                "session_id": session_id,
                "rejecting_agent": agent_name,
                "total_rounds": round_counter[session_id],
                "message": "Negotiation failed due to rejection"
            }),
            "topic": "mechanism_response"
        }
    
    elif message_type == "ready":
        # Agent is ready (typically from seller)
        return {
            "result": json.dumps({
                "type": "agent_ready",
                "session_id": session_id,
                "agent": agent_name,
                "message": f"{agent_name} is ready"
            }),
            "topic": "mechanism_response"
        }
    
    return {"error": f"Unknown message type: {message_type}", "topic": "mechanism_response"}


def end_negotiation(message: dict) -> dict:
    """End a negotiation session"""
    session_id = message.get("session_id")
    
    if session_id not in negotiations:
        return {"error": "Unknown negotiation session"}
    
    negotiation = negotiations[session_id]
    negotiation["status"] = "ended"
    
    # Notify agents
    end_message = {
        "type": "end_negotiation",
        "session_id": session_id,
        "reason": "mechanism_requested"
    }
    
    mqtt_client.publish(buyer_topic, json.dumps(end_message))
    mqtt_client.publish(seller_topic, json.dumps(end_message))
    
    return {
        "type": "negotiation_ended",
        "session_id": session_id,
        "total_rounds": round_counter.get(session_id, 0),
        "message": "Negotiation ended by mechanism"
    }


def get_negotiation_status(message: dict) -> dict:
    """Get status of a negotiation session"""
    session_id = message.get("session_id")
    
    if session_id not in negotiations:
        return {"error": "Unknown negotiation session"}
    
    negotiation = negotiations[session_id]
    
    return {
        "type": "status_report",
        "session_id": session_id,
        "status": negotiation["status"],
        "current_round": round_counter.get(session_id, 0),
        "max_rounds": negotiation["max_rounds"],
        "participants": negotiation["participants"],
        "start_time": negotiation["start_time"],
        "final_agreement": negotiation.get("final_agreement"),
        "total_rounds_completed": len(negotiation["rounds"])
    }


def on_destroy() -> dict | None:
    """
    Cleanup when mechanism is shutting down.
    
    Returns:
        dict | None: Final status and statistics
    """
    global mqtt_client
    
    # End all active negotiations
    for session_id, negotiation in negotiations.items():
        if negotiation["status"] == "active":
            end_message = {
                "type": "end_negotiation", 
                "session_id": session_id,
                "reason": "mechanism_shutdown"
            }
            mqtt_client.publish(buyer_topic, json.dumps(end_message))
            mqtt_client.publish(seller_topic, json.dumps(end_message))
    
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    
    return {
        "agent": "mechanism",
        "final_status": "destroyed",
        "negotiations_handled": len(negotiations),
        "active_negotiations": len([n for n in negotiations.values() if n["status"] == "active"]),
        "completed_negotiations": len([n for n in negotiations.values() if n["status"] == "completed"]),
        "config_used": config
    }


# Test code
if __name__ == "__main__":
    # Test configuration
    test_config = {
        "mqtt_broker": "localhost",
        "mqtt_port": 1883,
        "mechanism_config": {
            "max_rounds": 5,
            "timeout_seconds": 30,
            "outcome_space": {
                "issues": {
                    "price": {"values": [100, 150, 200, 250, 300]},
                    "quantity": {"values": [1, 10]}
                }
            }
        }
    }
    
    on_create(test_config)
    
    # Test starting a negotiation
    start_message = {
        "type": "start_negotiation",
        "session_id": "test_mechanism_session",
        "participants": ["buyer", "seller"]
    }
    
    result = on_receive([start_message])
    print("Mechanism Agent Test Result:")
    print(result["result"])
    
    # Test getting status
    status_message = {
        "type": "get_status",
        "session_id": "test_mechanism_session"
    }
    
    status_result = on_receive([status_message])
    print("\\nMechanism Status:")
    print(status_result["result"])
    
    on_destroy()