"""
Test script for MQTT-based negotiation agents
Tests the buyer, seller, and mechanism agents working together
"""

import json
import time
import threading
import sys
import os

# Add metaagents to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from buyer_agent import on_create as buyer_create, on_receive as buyer_receive, on_destroy as buyer_destroy
    from seller_agent import on_create as seller_create, on_receive as seller_receive, on_destroy as seller_destroy  
    from mechanism_agent import on_create as mechanism_create, on_receive as mechanism_receive, on_destroy as mechanism_destroy
    print("✓ Successfully imported all agents")
except ImportError as e:
    print(f"❌ Failed to import agents: {e}")
    print("Note: This test requires paho-mqtt library. Install with: pip install paho-mqtt")
    # Continue with basic functionality test
    buyer_create = seller_create = mechanism_create = None
    buyer_receive = seller_receive = mechanism_receive = None
    buyer_destroy = seller_destroy = mechanism_destroy = None


def test_agent_basic_functionality():
    """Test basic agent functionality without MQTT"""
    print("\\n=== Testing Basic Agent Functionality ===")
    
    # Test configurations
    buyer_config = {
        "negotiation_config": {
            "max_price": 250,
            "min_quantity": 1,
            "max_quantity": 10,
            "reserved_value": 0.3
        }
    }
    
    seller_config = {
        "negotiation_config": {
            "min_price": 150,
            "max_quantity": 8,
            "min_quantity": 1,
            "reserved_value": 0.25
        }
    }
    
    mechanism_config = {
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
    
    # Test buyer
    if buyer_create and buyer_receive:
        print("\\n--- Testing Buyer Agent ---")
        init_result = buyer_create(buyer_config)
        print(f"Buyer initialization: {init_result}")
        
        # Test negotiation start
        start_message = {
            "type": "start_negotiation",
            "session_id": "test_session_1",
            "outcome_space": {
                "issues": {
                    "price": {"values": [100, 150, 200, 250, 300]},
                    "quantity": {"values": [1, 10]}
                }
            }
        }
        
        result = buyer_receive([start_message])
        response = json.loads(result["result"])
        print(f"Buyer start negotiation: {response}")
        
        # Test buyer's response to a seller offer
        seller_offer_message = {
            "type": "offer",
            "session_id": "test_session_1",
            "offer": {"price": 200, "quantity": 5}
        }
        
        result = buyer_receive([seller_offer_message])
        response = json.loads(result["result"])
        print(f"Buyer response to offer: {response}")
    
    # Test seller
    if seller_create and seller_receive:
        print("\\n--- Testing Seller Agent ---")
        init_result = seller_create(seller_config)
        print(f"Seller initialization: {init_result}")
        
        # Test seller negotiation start
        start_message = {
            "type": "start_negotiation",
            "session_id": "test_session_2",
            "outcome_space": {
                "issues": {
                    "price": {"values": [100, 150, 200, 250, 300]},
                    "quantity": {"values": [1, 10]}
                }
            }
        }
        
        result = seller_receive([start_message])
        response = json.loads(result["result"])
        print(f"Seller start negotiation: {response}")
        
        # Test seller's response to a buyer offer
        buyer_offer_message = {
            "type": "offer",
            "session_id": "test_session_2",
            "offer": {"price": 120, "quantity": 8}
        }
        
        result = seller_receive([buyer_offer_message])
        response = json.loads(result["result"])
        print(f"Seller response to offer: {response}")
    
    # Test mechanism
    if mechanism_create and mechanism_receive:
        print("\\n--- Testing Mechanism Agent ---")
        init_result = mechanism_create(mechanism_config)
        print(f"Mechanism initialization: {init_result}")
        
        # Test starting negotiation
        start_message = {
            "type": "start_negotiation",
            "session_id": "test_mechanism_session",
            "participants": ["buyer", "seller"]
        }
        
        result = mechanism_receive([start_message])
        response = json.loads(result["result"])
        print(f"Mechanism start negotiation: {response}")
        
        # Test getting status
        status_message = {
            "type": "get_status",
            "session_id": "test_mechanism_session"
        }
        
        result = mechanism_receive([status_message])
        response = json.loads(result["result"])
        print(f"Mechanism status: {response}")


def test_negotiation_scenario():
    """Test a complete negotiation scenario"""
    print("\\n=== Testing Complete Negotiation Scenario ===")
    
    if not all([buyer_create, seller_create, mechanism_create, 
               buyer_receive, seller_receive, mechanism_receive]):
        print("❌ Cannot run full scenario test - agents not properly loaded")
        return
    
    # Initialize all agents
    buyer_config = {
        "negotiation_config": {
            "max_price": 220,
            "min_quantity": 1,
            "max_quantity": 10,
            "reserved_value": 0.4
        }
    }
    
    seller_config = {
        "negotiation_config": {
            "min_price": 180,
            "max_quantity": 8,
            "min_quantity": 1,
            "reserved_value": 0.3
        }
    }
    
    mechanism_config = {
        "mechanism_config": {
            "max_rounds": 5,
            "outcome_space": {
                "issues": {
                    "price": {"values": [100, 150, 200, 250, 300]},
                    "quantity": {"values": [1, 10]}
                }
            }
        }
    }
    
    # Initialize agents (without MQTT for this test)
    buyer_config["mqtt_broker"] = None  # Disable MQTT for testing
    seller_config["mqtt_broker"] = None
    mechanism_config["mqtt_broker"] = None
    
    buyer_create(buyer_config)
    seller_create(seller_config)
    mechanism_create(mechanism_config)
    
    session_id = "scenario_test_session"
    
    # 1. Start negotiation
    print("\\n1. Starting negotiation...")
    start_msg = {
        "type": "start_negotiation",
        "session_id": session_id,
        "outcome_space": mechanism_config["mechanism_config"]["outcome_space"]
    }
    
    # Buyer starts
    buyer_result = buyer_receive([start_msg])
    buyer_response = json.loads(buyer_result["result"])
    print(f"Buyer initial: {buyer_response}")
    
    if buyer_response.get("type") == "offer":
        initial_offer = buyer_response["offer"]
        print(f"Initial buyer offer: {initial_offer}")
        
        # 2. Seller responds to buyer's offer
        print("\\n2. Seller evaluates buyer's offer...")
        seller_msg = {
            "type": "offer",
            "session_id": session_id,
            "offer": initial_offer
        }
        
        seller_result = seller_receive([seller_msg])
        seller_response = json.loads(seller_result["result"])
        print(f"Seller response: {seller_response}")
        
        if seller_response.get("type") == "offer":
            # 3. Buyer responds to seller's counter-offer
            print("\\n3. Buyer evaluates seller's counter-offer...")
            counter_offer = seller_response["offer"]
            buyer_msg = {
                "type": "offer", 
                "session_id": session_id,
                "offer": counter_offer
            }
            
            final_buyer_result = buyer_receive([buyer_msg])
            final_buyer_response = json.loads(final_buyer_result["result"])
            print(f"Final buyer response: {final_buyer_response}")
            
            if final_buyer_response.get("type") == "accept":
                print("\\n✓ Negotiation successful! Agreement reached.")
                print(f"Final agreement: {final_buyer_response.get('offer')}")
            else:
                print("\\n⚠ Negotiation continues or failed")
        
        elif seller_response.get("type") == "accept":
            print("\\n✓ Seller accepted buyer's initial offer!")
            print(f"Final agreement: {initial_offer}")
    
    # Cleanup
    if buyer_destroy:
        buyer_destroy()
    if seller_destroy:
        seller_destroy()
    if mechanism_destroy:
        mechanism_destroy()


def main():
    """Run all tests"""
    print("MQTT-Based Negotiation Agents Test Suite")
    print("=" * 50)
    
    # Run basic functionality tests
    test_agent_basic_functionality()
    
    # Run complete scenario test
    test_negotiation_scenario()
    
    print("\\n" + "=" * 50)
    print("Test completed!")
    print("\\nNote: For full MQTT functionality, ensure:")
    print("1. MQTT broker is running (e.g., mosquitto)")
    print("2. paho-mqtt library is installed")
    print("3. Agents are running in separate processes for real MQTT communication")


if __name__ == "__main__":
    main()