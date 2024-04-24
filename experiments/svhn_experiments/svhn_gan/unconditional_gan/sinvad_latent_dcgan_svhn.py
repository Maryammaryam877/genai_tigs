import torch
import os
#import wandb
import numpy as np
from PIL import Image
import torchvision.utils as vutils
import matplotlib.pyplot as plt

from dcgan.svhngan import Generator
from classifier.model import VGGNet

#run = wandb.init(project="Sinvad_latent_based_svhnDCGAN")
def save_image(tensor, filename):
    """
    Save a tensor as an image
    """
    img = vutils.make_grid(tensor, normalize=True)  # Normalize ensures [0,1] range
    img = img.mul(255).add_(0.5).clamp_(0, 255).permute(1, 2, 0)
    # Now, transfer the tensor to CPU and convert it to numpy array for saving
    img = img.to('cpu', torch.uint8).numpy()  # Convert tensor to numpy array in [0,255] range
    img = Image.fromarray(img)
    img.save(filename)

# Set device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
img_size = 32 * 32 * 1
# Load the trained models
G = Generator(ngpu=1, nz=100, nc=3).to(device)  # Modify the Generator architecture
classifier = VGGNet().to(device)

# Load the weights of the models
G.load_state_dict(
    torch.load(
        "./dcgan/weights/net1G_epochsvhn_179.pth",
        map_location=device,
    )
)
classifier.load_state_dict(
    torch.load(
        "./classifier/SVHN_vggnet.pth",
        map_location=device,
    )
)
# Set models to evaluation mode
G.eval()
classifier.eval()
result_dir = "./result_svhn_dcgan"
# Subdirectory for original images
original_images_dir = os.path.join(result_dir, "original_images")
os.makedirs(result_dir, exist_ok=True)
os.makedirs(original_images_dir, exist_ok=True)
def calculate_fitness(logit, label):
    expected_logit = logit[label]
    # Select the two best indices [perturbed logit]
    best_indices = np.argsort(-logit)[:2]
    best_index1, best_index2 = best_indices
    if best_index1 == label:
        best_but_not_expected = best_index2
    else:
        best_but_not_expected = best_index1
    new_logit = logit[best_but_not_expected]
    # Compute fitness value
    fitness = expected_logit - new_logit
    return fitness


image_info = []
predictions = []
num_samples = 100
gen_num = 250
pop_size = 25
best_left = 10
min_val = -4.24642562866211
max_val= 4.22495269775391
perturbation_size = 0.00847137832641602  # Default perturbation size
initial_perturbation_size = 0.016942756652832  # Initial perturbation size

# Generate a random latent vector
latent_space = torch.randn(num_samples, 100, 1, 1).to(device)
all_img_lst = []
for i in range(num_samples):
    # Generate the non-perturbed image
    original_lv = latent_space[i].unsqueeze(0)
    original_image = G(original_lv)
    original_logit = classifier(original_image).squeeze().detach().cpu().numpy()
    # Convert the tensor to a NumPy array
    original_image_np = original_image.squeeze().detach().cpu().numpy()

    # Define the filename that includes the label
    filename = f"original_image_{i}_X_{original_label}.npy"

    # Define the full path for saving the file
    file_path = os.path.join(original_images_dir, filename)

    # Save the image data as a NumPy file
    np.save(file_path, original_image_np)

    ### Initialize optimization ###
    init_pop = [
       original_lv.unsqueeze(0)
       + initial_perturbation_size * torch.randn_like(original_lv)
       for _ in range(pop_size)
    ]
    now_pop = init_pop
    prev_best = np.inf
    best_fitness_score = np.inf
    best_image_tensor = None
    best_image_index = -1
    now_best_values = []
    ###GA_algorithm###
    for g_idx in range(gen_num):
        indivs_lv = torch.cat(now_pop, dim=0).view(-1, 100, 1, 1)
        Gen_imgs = G(indivs_lv)
        all_logits = classifier(Gen_imgs).squeeze().detach().cpu().numpy()
        fitness_scores = [
            calculate_fitness(all_logits[k_idx], original_label)
            for k_idx in range(pop_size)
        ]

        # Finding the minimum fitness score in this generation
        current_min_index = np.argmin(fitness_scores)
        current_min_fitness = fitness_scores[current_min_index]

        # Update global minimum if the current score is lower
        if current_min_fitness < best_fitness_score:
            best_fitness_score = current_min_fitness
            best_image_tensor = Gen_imgs[current_min_index].cpu().detach()
            best_image_index = current_min_index

        # Perform selection
        selected_indices = sorted(
            range(len(fitness_scores)),
            key=lambda i: fitness_scores[i],
            reverse=True,
        )[-best_left:]

        # Consider the lowest fitness score
        now_best = np.min(fitness_scores)

        parent_pop = [now_pop[idx] for idx in selected_indices]

        # Perform crossover and mutation
        print(
            "now_best "
            + str(now_best)
            + ", Average_score "
            + str(np.mean(fitness_scores))
        )
        # Log fitness scores
        #  wandb.log({"Fitness Score": now_best, "Sample Index": i, "Iteration": g_idx})

        if now_best < 0:
            break
        elif now_best == prev_best:
            perturbation_size *= 2
        else:
            perturbation_size = initial_perturbation_size

        k_pop = []
        for k_idx in range(pop_size - best_left):
            mom_idx, pop_idx = np.random.choice(best_left, size=2, replace=False)
            spl_idx = np.random.choice(100, size=1)[0]
            k_gene = torch.cat(
                [parent_pop[mom_idx][:, :spl_idx], parent_pop[pop_idx][:, spl_idx:]],
                dim=1,
            )  # crossover

            # Mutation
            diffs = (k_gene != original_lv).float().to(device)
            k_gene += (
                perturbation_size * torch.randn(k_gene.size()).to(device) * diffs
            )  # random adding noise only to diff place
           # k_gene = interp_mask * original_lv + (1 - interp_mask) * k_gene
            k_pop.append(k_gene)

        # Current population is obtained combining the parent pop and its offspring
        now_pop = parent_pop + k_pop
        prev_best = now_best
        now_pop = [torch.clamp(tensor, min=min_val, max=max_val) for tensor in now_pop]

     # Assuming best_image_tensor is tensor that needs to be processed
    mod_best_image_tensor = best_image_tensor.to(device)  # Move tensor to the appropriate device if not already
    mod_best_image_np = best_image_tensor.cpu().detach().numpy()  # Convert tensor to numpy array after moving to CPU

    # Assuming classifier is already defined and appropriate for the tensor as is
    final_bound_logits = classifier(mod_best_image_tensor.unsqueeze(0))  # Ensure tensor is in correct shape for classifier
    predicted_best_label = torch.argmax(final_bound_logits, dim=1).item()

    # Define the path for saving the numpy file with detailed filename
    image_path_np = os.path.join(
        result_dir,
        f"image_{i}_iteration{g_idx}_X{original_label}_Y{predicted_best_label}.npy"
    )

    # Save the numpy array to a file
    np.save(image_path_np, mod_best_image_np)
    
    all_img_lst.append(mod_best_image_np)


# Save all generated images as a numpy array
all_imgs = np.vstack(all_img_lst)
np.save(os.path.join(result_dir, "bound_imgs_svhn_dcgan.npy"), all_imgs)


