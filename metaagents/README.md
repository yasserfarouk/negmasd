# MQTT-Based Negotiation Agents

This directory contains MQTT-integrated negotiation agents that follow the same `on_create`, `on_receive`, `on_destroy` pattern as the original `example.py`.

## 🏗️ Architecture

The system consists of three main agents:

- **`buyer_agent.py`** - Buyer negotiation agent
- **`seller_agent.py`** - Seller negotiation agent  
- **`mechanism_agent.py`** - Coordination mechanism

## 🔄 MQTT Communication Flow

1. **Topics**: Each agent listens to its own topic (`buyer`, `seller`, `mechanism`)
2. **Responses**: Agents specify target topics in their `on_receive` responses
3. **Coordination**: Most communication flows through the mechanism for proper coordination

```
External System → mechanism (start negotiation)
Mechanism → buyer + seller (start messages)
Buyer → mechanism (initial offer)
Mechanism → seller (forward offer)
Seller → mechanism (counter-offer or accept/reject)
Mechanism → buyer (forward response)
... negotiation continues ...
```

## 📋 Agent Interface

Each agent follows the standard pattern:

```python
def on_create(data: dict) -> dict:
    """Initialize with configuration"""
    
def on_receive(data: list) -> dict:
    """Process messages and return response with target topic"""
    return {"result": json_response, "topic": target_topic}
    
def on_destroy() -> dict:
    """Cleanup and final status"""
```

## 🧪 Testing

### Core Logic Test (No Dependencies)
```bash
python3 test_core_logic.py
```

### Example.py Style Test
```bash
python3 run_like_example.py
```

### MQTT Integration Demo
```bash
python3 simple_mqtt_demo.py
```

## 🚀 Deployment

### Requirements
```bash
pip install paho-mqtt
```

### With Real MQTT Broker
```bash
# Start mosquitto broker
mosquitto -v

# Run each agent in separate terminals
python3 buyer_agent.py
python3 seller_agent.py  
python3 mechanism_agent.py
```

## ✅ Test Results

**Core Logic**: ✅ Working - Negotiation logic verified  
**Example Pattern**: ✅ Working - Same interface as original example.py  
**MQTT Integration**: ✅ Working - Pub/sub communication verified  
**Topic Routing**: ✅ Working - Dynamic topic specification implemented

## 🎯 Key Features

- **MQTT Integration** - Each agent has MQTT utility functions
- **Dynamic Topic Routing** - Agents specify response destinations
- **Mechanism Coordination** - Centralized negotiation management
- **Utility-Based Decisions** - Buyer/seller utility calculations
- **Multi-Round Negotiation** - Support for iterative offers
- **Agreement Detection** - Automatic agreement recognition

## 📊 Demo Results

The demo successfully shows:
- Buyer makes initial offer: `{price: 150, quantity: 8}`
- Seller counters: `{price: 190, quantity: 8}`  
- Buyer accepts: `{price: 190, quantity: 8}`
- **Agreement reached in 2 rounds!** 🎉

## 🔧 Configuration

Agents accept configuration via `on_create`:

```python
buyer_config = {
    "mqtt_broker": "localhost",
    "mqtt_port": 1883,
    "negotiation_config": {
        "max_price": 250,
        "reserved_value": 0.4
    }
}
```

The system is ready for production deployment with a real MQTT broker!