import gurobipy as gp
from gurobipy import GRB
import numpy as np

def solve_vrp(locations, weights, service_times, num_vehicles, vehicle_capacity, t_max, time_windows, restricted_customers):
    """
    Solve the Vehicle Routing Problem using Mixed Integer Linear Programming.
    
    Parameters:
    - locations: Array of [x,y] coordinates for depot (index 0) and customers
    - weights: Array of package weights for each customer
    - service_times: Array of service times (depot at index 0 should be 0)
    - num_vehicles: Number of available vehicles
    - vehicle_capacity: Capacity of each vehicle
    - t_max: Maximum allowed route duration
    
    Returns:
    - model: The solved Gurobi model
    - solution_info: Dictionary containing solution metrics
    """
    # Calculate number of customers
    num_customers = len(weights)
    
    # Calculate travel times (Euclidean distances)
    travel_times = {(i,j): np.sqrt(np.sum((locations[i] - locations[j])**2))
                   for i in range(num_customers + 1)
                   for j in range(num_customers + 1) if i != j}

    # Sets
    C = range(1, num_customers + 1)  # Customers (1 to n)
    K = range(num_vehicles)          # Vehicles
    V = range(num_customers + 1)     # All nodes (0 is depot)

    # Create a new model
    model = gp.Model('VRP')

    # Create variables
    x = model.addVars([(i,j,k) for i in V for j in V for k in K if i != j],
                     vtype=GRB.BINARY, name='x')
    y = model.addVars([(i,k) for i in V for k in K],
                            vtype=GRB.CONTINUOUS, name='y')
    d = model.addVars([(j,k) for j in V for k in K],
                     vtype=GRB.CONTINUOUS, name='d')
    # Customer assignment variable (1 if customer i is served by vehicle k)
    z = model.addVars([(i,k) for i in C for k in K],
                     vtype=GRB.BINARY, name='z')


    # Set objective: Minimize total travel time
    model.setObjective(
        gp.quicksum(travel_times[i,j] * x[i,j,k] 
                    for i in V 
                    for j in V 
                    for k in K 
                    if i != j),
        GRB.MINIMIZE
    )

    # 1. Each customer is served exactly once
    for i in C:
        model.addConstr(gp.quicksum(x[j,i,k] for j in V for k in K if j != i) == 1)

    # 2. Flow conservation for customers
    for i in C:
        for k in K:
            model.addConstr(
                gp.quicksum(x[j,i,k] for j in V if j != i) ==
                gp.quicksum(x[i,j,k] for j in V if j != i)
            )

    # 3. Depot flow (each vehicle starts and ends at depot)
    for k in K:
        model.addConstr(
            gp.quicksum(x[0,j,k] for j in C) ==
            gp.quicksum(x[j,0,k] for j in C)
        )
        # Each vehicle can exit the depot at most once
        model.addConstr(gp.quicksum(x[0,j,k] for j in C) <= 1)

    # 4. Vehicle capacity
    for k in K:
        model.addConstr(
            gp.quicksum(weights[i-1] * gp.quicksum(x[j,i,k] for j in V if j != i) 
                        for i in C) <= vehicle_capacity
        )

    # 5. Time constraints with Big-M (excluding depot returns)
    M = 2 * sum(travel_times.values()) + 2 * sum(service_times)
    for k in K:
        for i in V:
            for j in V:
                if i != j and j != 0:
                    model.addConstr(
                        y[j,k] >= y[i,k] + travel_times[i,j] + service_times[i] -
                        M * (1 - x[i,j,k]),
                        name=f'time_{i}_{j}_{k}'
                        )

    # 6. Route duration constraints
    for k in K:
        for j in V:
            if j != 0:
                model.addConstr(
                    d[j,k] >= y[j,k] + service_times[j] + travel_times[j,0] - 
                    M * (1 - x[j,0,k]),
                    name=f'route_duration_{j}_{k}'
                )

    # 7. Initial departure from depot
    for k in K:
        model.addConstr(y[0,k] == 0)

    # 8. Maximum route duration
    for k in K:
        for j in V:
            if j != 0:
                model.addConstr(d[j,k] <= t_max * x[j,0,k],
                    name=f'max_route_duration_{j}_{k}'
                )

    # 9. Link customer assignment variables to route variables
    for i in C:
        for k in K:
            model.addConstr(
                z[i,k] == gp.quicksum(x[j,i,k] for j in V if j != i),
                name=f'assign_{i}_{k}'
            )

    # 10. Time window constraints
    for i in C:
        a_i, b_i = time_windows[i-1]  # Get time window for customer i (adjust index)
        for k in K:
            # Service must start within the time window
            model.addConstr(
                y[i,k] >= a_i * z[i,k],
                name=f'time_window_min_{i}_{k}'
            )
            model.addConstr(
                y[i,k] <= b_i + M * (1 - z[i,k]),
                name=f'time_window_max_{i}_{k}'
            )
    
    # 11. Customer service restrictions
    for i, j, l in restricted_customers:
        for k in K:
            # If customers i and j are both served by vehicle k, then l cannot be served by k
            model.addConstr(
                z[i,k] + z[j,k] + z[l,k] <= 2,
                name=f'restriction_{i}_{j}_{l}_{k}'
            )

    # Optimize the model
    model.optimize()
    
    # Prepare solution information
    solution_info = {}
    
    if model.status == GRB.OPTIMAL:
        # Calculate total travel time
        total_travel_time = sum(travel_times[i,j] * x[i,j,k].X 
                        for i in V for j in V for k in K if i != j)
        
        # Calculate total service time
        total_service_time = sum(service_times[i] * sum(x[j,i,k].X 
                           for j in V for k in K if j != i) 
                           for i in C)
        
        # Get vehicle routes
        routes = []
        starting_times = []
        for k in K:
            if any(x[0,j,k].X > 0.5 for j in C):
                route = [0]  # Start at depot
                times = [0.0]
                current = 0
                while True:
                    next_node = None
                    for j in V:
                        if j != current and x[current,j,k].X > 0.5:
                            next_node = j
                            route.append(j)
                            if j != 0:
                                times.append(y[j,k].X)
                            break
                    if next_node is None or next_node == 0:
                        break
                    current = next_node
                routes.append(route)
                starting_times.append(times)
        
        # Calculate vehicle loads
        vehicle_loads = []
        for k in K:
            load = sum(weights[i-1] * sum(x[j,i,k].X for j in V if j != i) for i in C)
            if load > 0:
                vehicle_loads.append(load)
        
        # Calculate route durations
        route_durations = []
        for k in K:
            max_duration = 0
            for j in C:
                if x[j,0,k].X > 0.5:  # If j is the last customer before returning to depot
                    duration = y[j,k].X + service_times[j] + travel_times[j,0]
                    max_duration = max(max_duration, duration)
            if max_duration > 0:
                route_durations.append(max_duration)
        
        # Store solution information
        solution_info = {
            'status': 'Optimal',
            'total_travel_time': total_travel_time,
            'total_service_time': total_service_time,
            'total_operational_time': total_travel_time + total_service_time,
            'routes': routes,
            'starting_times': starting_times,
            'vehicle_loads': vehicle_loads,
            'route_durations': route_durations,
            'vehicles_used': len(routes),
            'vehicles_available': num_vehicles,
            'capacity_utilization': sum(vehicle_loads) / (len(routes) * vehicle_capacity)
        }
    else:
        solution_info = {'status': 'No optimal solution found', 'model_status': model.status}
    
    return model, solution_info

def print_solution(solution_info, time_windows):
    """Print the solution in a clean, structured format."""
    if solution_info['status'] != 'Optimal':
        print(f"Model status: {solution_info['model_status']}")
        print("No optimal solution found")
        return
    
    print("\n===== VRP SOLUTION SUMMARY =====")
    print(f"Total travel time: {solution_info['total_travel_time']:.2f}")
    print(f"Total service time: {solution_info['total_service_time']:.2f}")
    print(f"Total operational time: {solution_info['total_operational_time']:.2f}")
    print(f"Vehicles used: {solution_info['vehicles_used']}/{solution_info['vehicles_available']}")
    print(f"Capacity utilization: {solution_info['capacity_utilization']*100:.1f}%")
    
    print("\n===== ROUTE DETAILS =====")
    for i, route in enumerate(solution_info['routes']):
        print(f"Vehicle {i+1}: {' → '.join(map(str, route))}")
        print(f"  Load: {solution_info['vehicle_loads'][i]:.1f}")
        print(f"  Duration: {solution_info['route_durations'][i]:.2f}")

        # Print starting times and time windows
        print("  Starting times:")
        for j, node in enumerate(route):
            if node != 0:  # Skip depot
                starting_time = solution_info['starting_times'][i][j]
                a_c, b_c = time_windows[node-1]
                print(f"    Node {node}: {starting_time:.2f} (Window: [{a_c:.2f}, {b_c:.2f}])")
    
    print("\n===== PERFORMANCE METRICS =====")
    print(f"Average route duration: {sum(solution_info['route_durations'])/len(solution_info['route_durations']):.2f}")
    print(f"Maximum route duration: {max(solution_info['route_durations']):.2f}")

if __name__ == "__main__":
    # Problem data
    locations = np.array([
        [0, 0],    # Depot
        [2, 7],    # Customer 1
        [5, 19],   # Customer 2
        [2, 18],  # Customer 3
        [4, 5],    # Customer 4
        [6, 12]    # Customer 5
    ])
    
    weights = np.array([20, 30, 10, 44, 32])  # Customer weights
    service_times = np.array([0, 10, 15, 5, 12, 16])  # Depot and customer service times
    
    time_windows = [
        (10, 100),  # Customer 1: [10, 100]
        (70, 150),  # Customer 2: [50, 150]
        (40, 80),   # Customer 3: [30, 80]
        (60, 70),   # Customer 4: [15, 70]
        (50, 110)   # Customer 5: [40, 110]
    ]
    
    # Restricted customer combinations
    # Format: (i, j, l) where l cannot be served if i and j are served by the same vehicle
    restricted_customers = [
        (1, 2, 3),  # Customer 3 cannot be served if 1 and 2 are served by the same vehicle
        (2, 4, 5),  # Customer 5 cannot be served if 2 and 4 are served by the same vehicle
        (3, 5, 1)   # Customer 1 cannot be served if 3 and 5 are served by the same vehicle
    ]

    num_customers = len(weights)
    num_vehicles = 3
    vehicle_capacity = 50
    t_max = 1000
    
    # Solve the VRP
    model, solution = solve_vrp(
        locations, 
        weights, 
        service_times, 
        num_vehicles, 
        vehicle_capacity, 
        t_max,
        time_windows,
        restricted_customers
    )
    
    # Display the results
    print_solution(solution, time_windows)